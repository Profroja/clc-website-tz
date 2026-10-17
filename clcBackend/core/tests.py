from django.test import TestCase

from accounts.models import User


class AdminOverviewTests(TestCase):
    def sign_in(self):
        User.objects.create_superuser('boss@clc.tz', 'Pw-12345-xyz', full_name='Boss')
        login = self.client.post('/api/auth/login/', {'email': 'boss@clc.tz', 'password': 'Pw-12345-xyz'}, content_type='application/json').json()
        return self.client.post('/api/auth/select-role/', {'membership_id': login['memberships'][0]['id']},
                                content_type='application/json', HTTP_AUTHORIZATION=f"Bearer {login['pick_role_token']}").json()['access']

    def test_requires_admin(self):
        self.assertEqual(self.client.get('/api/admin/overview/').status_code, 401)

    def test_counts(self):
        token = self.sign_in()
        data = self.client.get('/api/admin/overview/', HTTP_AUTHORIZATION=f'Bearer {token}').json()
        self.assertEqual(data['users'], {'total': 1, 'not_signed_in_yet': 0})
        self.assertEqual(data['law_firms'], {'total': 0, 'approved': 0, 'awaiting_approval': 0})
        self.assertEqual(len(data['conversations_per_day']), 7)
        self.assertEqual(data['awaiting_firms'], [])

    def test_counts_real_rows(self):
        from clients.models import Client
        from conversations.models import BotFlow, Conversation
        token = self.sign_in()
        for i in range(2):
            Conversation.objects.create(client=Client.objects.create(wa_id=f'25570000000{i}'), channel='whatsapp')
        BotFlow.objects.create(name='Intake')
        data = self.client.get('/api/admin/overview/', HTTP_AUTHORIZATION=f'Bearer {token}').json()
        self.assertEqual(data['conversations_per_day'][-1]['v'], 2)
        self.assertEqual(data['flows'], {'total': 1, 'live': 0, 'with_changes': 0})


class PackagesApiTests(TestCase):
    def setUp(self):
        import shutil
        import tempfile
        from django.test import override_settings
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.tmp)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        User.objects.create_superuser('boss@clc.tz', 'Pw-12345-xyz', full_name='Boss')
        login = self.client.post('/api/auth/login/', {'email': 'boss@clc.tz', 'password': 'Pw-12345-xyz'}, content_type='application/json').json()
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer ' + self.client.post(
            '/api/auth/select-role/', {'membership_id': login['memberships'][0]['id']}, content_type='application/json',
            HTTP_AUTHORIZATION=f"Bearer {login['pick_role_token']}").json()['access']}

    multipart_ct = 'multipart/form-data; boundary=BoUnDaRy'

    def encode_multipart(self, data):
        from django.test.client import encode_multipart
        return encode_multipart('BoUnDaRy', data)

    def create(self, **over):
        data = {'title': 'Mkataba kati ya Mchimbaji Mdogo na Mwekezaji', 'description': 'Sheria za madini…',
                'items': '["Uhakiki wa PML", "Uandishi na kusaini Mkataba wa TSA", ""]', 'media_type': 'none', **over}
        return self.client.post('/api/admin/packages/', data, **self.auth)

    def test_create_list_and_public_view(self):
        res = self.create()
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['items'], ['Uhakiki wa PML', 'Uandishi na kusaini Mkataba wa TSA'])  # blanks dropped
        self.assertEqual(len(self.client.get('/api/admin/packages/', **self.auth).json()), 1)
        self.assertEqual(len(self.client.get('/api/packages/').json()), 1)
        self.assertEqual(self.client.get('/api/admin/packages/').status_code, 401)  # admin list needs a login

    def test_hidden_packages_are_not_public(self):
        pid = self.create(is_published='false').json()['id']
        self.assertEqual(self.client.get('/api/packages/').json(), [])
        self.client.patch(f'/api/admin/packages/{pid}/', {'is_published': 'true'}, content_type='application/json', **self.auth)
        self.assertEqual(len(self.client.get('/api/packages/').json()), 1)

    def test_hidden_package_picture_needs_the_admins_signed_link(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        img = SimpleUploadedFile('cover.png', b'\x89PNG fake', content_type='image/png')
        item = self.create(media_type='image', image=img, is_published='false').json()
        signed = item['image_url']
        self.assertIn('?sig=', signed)
        self.assertEqual(self.client.get(signed).status_code, 200)  # the admin portal can show it
        self.assertEqual(self.client.get(signed.split('?')[0]).status_code, 404)  # the public cannot
        self.assertEqual(self.client.get(signed.split('?')[0] + '?sig=forged').status_code, 404)
        self.client.patch(f"/api/admin/packages/{item['id']}/", {'is_published': 'true'}, content_type='application/json', **self.auth)
        public = self.client.get('/api/packages/').json()[0]
        self.assertNotIn('sig=', public['image_url'])  # public listings never carry the admin's signature

    def test_image_and_video(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        img = SimpleUploadedFile('cover.png', b'\x89PNG fake', content_type='image/png')
        res = self.create(media_type='image', image=img).json()
        self.assertEqual(res['media_type'], 'image')
        served = self.client.get(res['image_url'])  # served publicly, nothing else in storage is
        self.assertEqual((served.status_code, served['Content-Type']), (200, 'image/png'))

        bad = SimpleUploadedFile('x.exe', b'x', content_type='application/octet-stream')
        self.assertEqual(self.create(media_type='image', image=bad).status_code, 400)
        self.assertEqual(self.create(media_type='image').status_code, 400)  # image chosen but none uploaded

        pid = res['id']
        patch = lambda body: self.client.patch(f'/api/admin/packages/{pid}/', body, content_type='application/json', **self.auth)
        yt = patch({'media_type': 'youtube', 'video_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'}).json()
        self.assertEqual((yt['media_type'], yt['youtube_id'], yt['image_url']), ('youtube', 'dQw4w9WgXcQ', None))  # the image is dropped
        self.assertEqual(patch({'media_type': 'youtube', 'video_url': 'not a link'}).status_code, 400)

    def test_uploaded_video_streams_with_ranges(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = bytes(range(256)) * 40  # 10,240 bytes
        res = self.create(media_type='video', video=SimpleUploadedFile('clip.mp4', data, content_type='video/mp4'))
        self.assertEqual(res.status_code, 201, res.content)
        pkg = res.json()
        self.assertEqual((pkg['media_type'], pkg['image_url']), ('video', None))

        full = self.client.get(pkg['video_url'])
        self.assertEqual((full.status_code, full['Accept-Ranges'], int(full['Content-Length'])), (200, 'bytes', len(data)))
        self.assertEqual(b''.join(full.streaming_content), data)
        part = self.client.get(pkg['video_url'], HTTP_RANGE='bytes=100-199')
        self.assertEqual((part.status_code, part['Content-Range']), (206, f'bytes 100-199/{len(data)}'))
        self.assertEqual(b''.join(part.streaming_content), data[100:200])
        tail = self.client.get(pkg['video_url'], HTTP_RANGE='bytes=-50')
        self.assertEqual(b''.join(tail.streaming_content), data[-50:])
        self.assertEqual(self.client.get(pkg['video_url'], HTTP_RANGE=f'bytes={len(data) + 5}-').status_code, 416)

        bad = SimpleUploadedFile('clip.exe', b'x', content_type='application/octet-stream')
        self.assertEqual(self.create(media_type='video', video=bad).status_code, 400)
        self.assertEqual(self.create(media_type='video').status_code, 400)  # chosen, but nothing uploaded

        # switching to an image removes the video
        img = SimpleUploadedFile('c.png', b'\x89PNG', content_type='image/png')
        sw = self.client.patch(f"/api/admin/packages/{pkg['id']}/", self.encode_multipart({'media_type': 'image', 'image': img}),
                               content_type=self.multipart_ct, **self.auth).json()
        self.assertEqual((sw['media_type'], sw['video_url']), ('image', None))
    def test_edit_and_delete(self):
        pid = self.create().json()['id']
        res = self.client.patch(f'/api/admin/packages/{pid}/', {'title': 'New title', 'items': ['A', 'B']}, content_type='application/json', **self.auth)
        self.assertEqual((res.json()['title'], res.json()['items']), ('New title', ['A', 'B']))
        self.assertEqual(self.client.patch(f'/api/admin/packages/{pid}/', {'title': ' '}, content_type='application/json', **self.auth).status_code, 400)
        self.assertEqual(self.client.delete(f'/api/admin/packages/{pid}/', **self.auth).status_code, 204)
        self.assertEqual(self.client.get('/api/admin/packages/', **self.auth).json(), [])