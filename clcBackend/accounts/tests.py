from django.test import TestCase

from accounts.models import Membership, MembershipStatus, User
from tracking.models import SessionEndReason, UserSession


class AdminUsersApiTests(TestCase):
    def setUp(self):
        from django.core import mail as _mail  # noqa: F401
        from accounts.models import Organization
        User.objects.create_superuser('boss@clc.tz', 'Pw-12345-xyz', full_name='Boss')
        login = self.client.post('/api/auth/login/', {'email': 'boss@clc.tz', 'password': 'Pw-12345-xyz'}, content_type='application/json').json()
        self.token = self.client.post('/api/auth/select-role/', {'membership_id': login['memberships'][0]['id']},
                                      content_type='application/json', HTTP_AUTHORIZATION=f"Bearer {login['pick_role_token']}").json()['access']
        self.firm = Organization.objects.create(type='law_firm', name='PNJ Legal', status='approved')
        self.pending = Organization.objects.create(type='law_firm', name='Pending LLP', status='pending')

    def post(self, data):
        return self.client.post('/api/admin/users/', data, content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {self.token}')

    def test_only_registered_firms_are_offered(self):
        res = self.client.get('/api/admin/firms/', HTTP_AUTHORIZATION=f'Bearer {self.token}').json()
        self.assertEqual([f['name'] for f in res], ['PNJ Legal'])

    def test_advocate_needs_a_registered_firm_and_details(self):
        import shutil
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from onboarding.models import PracticeArea
        PracticeArea.objects.create(code='land', name_en='Land', name_sw='Ardhi')
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with override_settings(MEDIA_ROOT=tmp):
            base = {'full_name': 'Neema Lyimo', 'email': 'neema@pnj.co.tz', 'role': 'advocate'}
            full = {**base, 'organization_id': str(self.firm.id), 'roll_number': 'R-1', 'practising_certificate': 'PC-1',
                    'certificate_expires_on': '2027-01-01', 'practice_areas': ['land']}
            cert = lambda name='cert.pdf': SimpleUploadedFile(name, b'%PDF-1.4 scan', content_type='application/pdf')
            post = lambda data, **files: self.client.post('/api/admin/users/', data, HTTP_AUTHORIZATION=f'Bearer {self.token}', **files)  # multipart

            self.assertEqual(post(base).status_code, 400)  # no firm
            self.assertEqual(post({**base, 'organization_id': str(self.pending.id)}).status_code, 400)  # not approved
            self.assertEqual(post({**base, 'organization_id': str(self.firm.id)}).status_code, 400)  # no roll number etc.
            self.assertEqual(post(full).status_code, 400)  # no certificate file
            self.assertEqual(post({**full, 'certificate': cert('cert.exe')}).status_code, 400)  # wrong file type
            self.assertEqual(post({**full, 'practice_areas': [], 'certificate': cert()}).status_code, 400)  # no expertise
            ok = post({**full, 'certificate': cert()})
            self.assertEqual(ok.status_code, 201, ok.content)
            self.assertEqual(ok.json()['organization'], 'PNJ Legal')
            user = User.objects.get(email='neema@pnj.co.tz')
            self.assertTrue(user.must_change_password)
            profile = user.advocate_profile
            self.assertEqual([a.code for a in profile.practice_areas.all()], ['land'])
            self.assertTrue(profile.certificate_file.name.endswith('cert.pdf'))
            self.assertEqual(post({**full, 'certificate': cert()}).status_code, 400)  # already has the role
            other = post({**full, 'email': 'baraka@pnj.co.tz', 'full_name': 'Baraka', 'roll_number': 'R-2', 'practice_areas': [],
                          'other_areas': 'Maritime law', 'certificate': cert()})
            self.assertEqual(other.status_code, 201, other.content)  # an area that is not in the list is accepted
            profile.certificate_file.close()

    def test_admin_and_firm_admin_and_listing(self):
        self.assertEqual(self.post({'full_name': 'Amina', 'email': 'amina@clc.tz', 'role': 'clc_admin'}).status_code, 201)
        self.assertEqual(self.post({'full_name': 'Juma', 'email': 'juma@pnj.co.tz', 'role': 'firm_admin',
                                    'organization_id': str(self.firm.id)}).status_code, 201)
        rows = self.client.get('/api/admin/users/', HTTP_AUTHORIZATION=f'Bearer {self.token}').json()
        self.assertEqual({(r['email'], r['role']) for r in rows},
                         {('boss@clc.tz', 'clc_admin'), ('amina@clc.tz', 'clc_admin'), ('juma@pnj.co.tz', 'firm_admin')})

    def test_requires_admin(self):
        self.assertEqual(self.client.get('/api/admin/users/').status_code, 401)

    def test_edit_deactivate_and_reset_password(self):
        row = self.post({'full_name': 'Juma', 'email': 'juma@pnj.co.tz', 'role': 'firm_admin', 'organization_id': str(self.firm.id)}).json()
        auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        url = f"/api/admin/users/{row['id']}/"
        res = self.client.patch(url, {'full_name': 'Juma Ally', 'phone': '+255700000001'}, content_type='application/json', **auth)
        self.assertEqual((res.json()['full_name'], res.json()['phone']), ('Juma Ally', '+255700000001'))
        self.assertEqual(self.client.patch(url, {'email': 'boss@clc.tz'}, content_type='application/json', **auth).status_code, 400)  # taken

        reset = self.client.post(url + 'reset-password/', **auth).json()
        login = self.client.post('/api/auth/login/', {'email': 'juma@pnj.co.tz', 'password': reset['temporary_password']}, content_type='application/json')
        self.assertTrue(login.json()['must_change_password'])

        off = self.client.post(url + 'deactivate/', **auth).json()
        self.assertEqual((off['is_active'], off['status']), (False, 'inactive'))
        self.assertEqual(self.client.post('/api/auth/login/', {'email': 'juma@pnj.co.tz', 'password': reset['temporary_password']}, content_type='application/json').status_code, 401)
        self.assertTrue(self.client.post(url + 'activate/', **auth).json()['is_active'])

        boss = next(r for r in self.client.get('/api/admin/users/', **auth).json() if r['email'] == 'boss@clc.tz')
        self.assertEqual(self.client.post(f"/api/admin/users/{boss['id']}/deactivate/", **auth).status_code, 400)  # not yourself


class TokenLifecycleTests(TestCase):
    def setUp(self):
        # A superuser is automatically a CLC Admin (see accounts.signals).
        self.user = User.objects.create_superuser('boss@clc.tz', 'pw-12345-x', full_name='Boss')

    def post(self, path, data=None, token=None):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
        return self.client.post(f'/api/auth/{path}/', data or {}, content_type='application/json', **headers)

    def sign_in(self):
        login = self.post('login', {'email': 'BOSS@clc.tz', 'password': 'pw-12345-x'}).json()
        self.assertEqual(len(login['memberships']), 1)
        return self.post('select-role', {'membership_id': login['memberships'][0]['id']}, login['pick_role_token']).json()

    def test_login_then_role_gives_access_and_refresh(self):
        tokens = self.sign_in()
        self.assertIn('access', tokens)
        self.assertIn('refresh', tokens)
        self.assertEqual(tokens['membership']['role'], 'clc_admin')

    def test_wrong_password_is_refused(self):
        self.assertEqual(self.post('login', {'email': 'boss@clc.tz', 'password': 'bad'}).status_code, 401)

    def test_pick_role_token_cannot_be_refreshed(self):
        login = self.post('login', {'email': 'boss@clc.tz', 'password': 'pw-12345-x'}).json()
        self.assertEqual(self.post('refresh', {'refresh': login['pick_role_token']}).status_code, 401)

    def test_refresh_rotates_and_old_token_dies(self):
        tokens = self.sign_in()
        new = self.post('refresh', {'refresh': tokens['refresh']})
        self.assertEqual(new.status_code, 200)
        self.assertNotEqual(new.json()['refresh'], tokens['refresh'])
        # the new access token works on a protected endpoint
        self.assertEqual(self.post('logout', {'refresh': new.json()['refresh']}, new.json()['access']).status_code, 204)
        # the old refresh token was blacklisted by the rotation
        self.assertEqual(self.post('refresh', {'refresh': tokens['refresh']}).status_code, 401)

    def test_logout_ends_session_and_blocks_refresh(self):
        tokens = self.sign_in()
        self.assertEqual(self.post('logout', {'refresh': tokens['refresh']}, tokens['access']).status_code, 204)
        self.assertEqual(UserSession.objects.get().end_reason, SessionEndReason.SIGN_OUT)
        self.assertEqual(self.post('refresh', {'refresh': tokens['refresh']}).status_code, 401)

    def test_refresh_fails_when_role_is_suspended(self):
        tokens = self.sign_in()
        Membership.objects.update(status=MembershipStatus.SUSPENDED)
        res = self.post('refresh', {'refresh': tokens['refresh']})
        self.assertEqual((res.status_code, res.json()['detail']), (401, 'session_ended'))

    def test_garbage_refresh_token(self):
        self.assertEqual(self.post('refresh', {'refresh': 'nope'}).status_code, 401)
        self.assertEqual(self.post('refresh', {}).status_code, 401)
