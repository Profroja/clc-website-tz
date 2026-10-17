import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import User


class MediaApiTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        User.objects.create_superuser('boss@clc.tz', 'Pw-12345-xyz', full_name='Boss')
        login = self.client.post('/api/auth/login/', {'email': 'boss@clc.tz', 'password': 'Pw-12345-xyz'}, content_type='application/json').json()
        token = self.client.post('/api/auth/select-role/', {'membership_id': login['memberships'][0]['id']}, content_type='application/json',
                                 HTTP_AUTHORIZATION=f"Bearer {login['pick_role_token']}").json()['access']
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def cover(self):
        return SimpleUploadedFile('cover.png', b'\x89PNG fake', content_type='image/png')

    def create(self, **over):
        data = {'kind': 'podcast', 'title': 'Haki za Mchimbaji Mdogo', 'excerpt': 'Wakili anaeleza leseni ya PML.',
                'body': 'Aya ya kwanza.\n\nAya ya pili.', 'published_on': '2026-09-20', 'duration': '24:10',
                'player': 'none', 'cover': self.cover(), **over}
        return self.client.post('/api/admin/media/', data, **self.auth)

    def test_create_and_public_list(self):
        res = self.create()
        self.assertEqual(res.status_code, 201, res.content)
        item = res.json()
        self.assertEqual((item['kind'], item['player'], item['body']), ('podcast', 'none', ['Aya ya kwanza.', 'Aya ya pili.']))
        public = self.client.get('/api/media/').json()
        self.assertEqual([p['title'] for p in public], ['Haki za Mchimbaji Mdogo'])
        served = self.client.get(public[0]['cover_url'])
        self.assertEqual((served.status_code, served['Content-Type']), (200, 'image/png'))
        self.assertEqual(self.client.get('/api/admin/media/').status_code, 401)

    def test_validation(self):
        self.assertEqual(self.create(kind='radio').status_code, 400)
        self.assertEqual(self.create(title=' ').status_code, 400)
        self.assertEqual(self.create(duration='soon').status_code, 400)
        self.assertEqual(self.create(published_on='yesterday').status_code, 400)
        no_cover = self.client.post('/api/admin/media/', {'kind': 'machapisho', 'title': 'X', 'published_on': '2026-01-01', 'player': 'none'}, **self.auth)
        self.assertEqual(no_cover.status_code, 400)  # a cover is needed unless there is a YouTube video

    def test_youtube_needs_no_cover(self):
        res = self.client.post('/api/admin/media/', {'kind': 'mijadala', 'title': 'Mjadala', 'published_on': '2026-01-01', 'player': 'youtube',
                                                     'video_url': 'https://youtu.be/dQw4w9WgXcQ'}, **self.auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual((res.json()['player'], res.json()['youtube_id'], res.json()['cover_url']), ('youtube', 'dQw4w9WgXcQ', None))

    def test_uploaded_video_and_switching(self):
        data = bytes(range(256)) * 20
        item = self.create(kind='mijadala', player='video', video=SimpleUploadedFile('clip.mp4', data, content_type='video/mp4')).json()
        self.assertEqual(item['player'], 'video')
        part = self.client.get(item['video_url'], HTTP_RANGE='bytes=10-29')
        self.assertEqual((part.status_code, b''.join(part.streaming_content)), (206, data[10:30]))
        bad = self.create(player='video', video=SimpleUploadedFile('x.exe', b'x', content_type='application/octet-stream'))
        self.assertEqual(bad.status_code, 400)
        back = self.client.patch(f"/api/admin/media/{item['id']}/", {'player': 'youtube', 'video_url': 'dQw4w9WgXcQ'},
                                 content_type='application/json', **self.auth).json()
        self.assertEqual((back['player'], back['video_url']), ('youtube', None))  # the uploaded video is removed

    def test_hidden_edit_delete(self):
        item = self.create(is_published='false').json()
        self.assertEqual(self.client.get('/api/media/').json(), [])
        bare = item['cover_url'].split('?')[0]
        self.assertEqual(self.client.get(bare).status_code, 404)  # hidden items serve nothing to the public
        self.assertEqual(self.client.get(bare + '?sig=forged').status_code, 404)  # nor to a forged link
        self.assertEqual(self.client.get(item['cover_url']).status_code, 200)  # but the admin's signed link works, so the portal can show it
        edit = self.client.patch(f"/api/admin/media/{item['id']}/", {'title': 'Mpya', 'is_published': 'true'}, content_type='application/json', **self.auth).json()
        self.assertEqual(edit['title'], 'Mpya')
        self.assertEqual(len(self.client.get('/api/media/').json()), 1)
        self.assertEqual(self.client.delete(f"/api/admin/media/{item['id']}/", **self.auth).status_code, 204)
        self.assertEqual(self.client.get('/api/admin/media/', **self.auth).json(), [])

    def test_video_without_cover_and_removing_a_cover(self):
        item = self.create(kind='mijadala').json()  # starts with a cover
        self.assertIsNotNone(item['cover_url'])
        url = f"/api/admin/media/{item['id']}/"
        # removing the only picture is refused: the card would have nothing to show
        refused = self.client.patch(url, {'remove_cover': 'true'}, content_type='application/json', **self.auth)
        self.assertEqual(refused.status_code, 400)
        # with an uploaded video the cover can go: the website then shows a frame from the video
        clip = SimpleUploadedFile('clip.mp4', b'\x00' * 512, content_type='video/mp4')
        res = self.client.patch(url, self.encode_form({'player': 'video', 'video': clip, 'remove_cover': 'true'}),
                                content_type=self.form_ct, **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual((data['cover_url'], data['player']), (None, 'video'))
        public = self.client.get('/api/media/').json()[0]
        self.assertEqual((public['cover_url'], bool(public['video_url'])), (None, True))
        # a brand-new item may be a video with no cover at all
        fresh = self.client.post('/api/admin/media/', {'kind': 'podcast', 'title': 'Video only', 'published_on': '2026-02-02', 'player': 'video',
                                                       'video': SimpleUploadedFile('v.mp4', b'\x00' * 64, content_type='video/mp4')}, **self.auth)
        self.assertEqual(fresh.status_code, 201, fresh.content)

    form_ct = 'multipart/form-data; boundary=BoUnDaRy'

    def encode_form(self, data):
        from django.test.client import encode_multipart
        return encode_multipart('BoUnDaRy', data)
