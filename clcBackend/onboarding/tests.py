import shutil
import tempfile
from datetime import date, timedelta

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AdvocateProfile, Membership, Organization, OrgStatus, User
from notifications.models import Notification
from tracking.models import ActivityEvent

from .models import AppStatus, DocType, FirmApplication, REQUIRED_FIRM_DOCS
from .services import add_months, expiring_items

TMP = tempfile.mkdtemp()


def payload(**over):
    data = {
        'firm_name': 'PNJ Legal Consultants', 'brela_number': 'LF/2019/0412', 'registration_date': '2019-05-01',
        'tin': '112-384-920', 'licence_number': 'BL-9981', 'licence_expiry': str(date.today() + timedelta(days=400)),
        'region': 'Dar es Salaam', 'district': 'Ilala', 'ward': 'Kivukoni', 'street': 'Plot 22, Samora Avenue',
        'postal_address': 'P.O. Box 4661', 'phone_e164': '0712440120', 'whatsapp_e164': '+255712440120',
        'email': 'info@pnj.co.tz', 'contact_person': 'Juma Kileo', 'contact_person_phone': '0712440121',
        'practice_areas': [{'code': 'mining', 'is_primary': True}, {'code': 'tax', 'is_primary': False}],
        'regions_served': ['Dar es Salaam', 'Pwani'], 'languages': ['sw', 'en'],
        'partners': [{'full_name': 'Juma Kileo', 'email': 'juma@pnj.co.tz', 'phone_e164': '0712440121',
                      'is_managing_partner': True, 'is_authorized_rep': True}],
        'advocates': [{'full_name': 'Neema Lyimo', 'roll_number': 'ROLL-100', 'email': 'neema@pnj.co.tz',
                       'phone_e164': '0755000111', 'practising_certificate': 'PC-77',
                       'certificate_expiry': str(date.today() + timedelta(days=30)), 'practice_areas': ['mining']}],
    }
    data.update(over)
    return data


@override_settings(MEDIA_ROOT=TMP)
class OnboardingFlowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        User.objects.create_superuser('boss@clc.tz', 'Pw-12345-xyz', full_name='Boss')
        self.admin = self.sign_in('boss@clc.tz', 'Pw-12345-xyz')

    # -- helpers
    def post(self, path, data=None, token=None, applicant=None, **kw):
        headers = {}
        if token:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {token}'
        if applicant:
            headers['HTTP_X_APPLICANT_TOKEN'] = applicant
        return self.client.post(f'/api/{path}', data or {}, content_type=kw.pop('content_type', 'application/json'), **headers)

    def get(self, path, token=None, applicant=None):
        headers = {}
        if token:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {token}'
        if applicant:
            headers['HTTP_X_APPLICANT_TOKEN'] = applicant
        return self.client.get(f'/api/{path}', **headers)

    def sign_in(self, email, password):
        login = self.post('auth/login/', {'email': email, 'password': password}).json()
        return self.post('auth/select-role/', {'membership_id': login['memberships'][0]['id']}, login['pick_role_token']).json()['access']

    def upload(self, app_id, token, doc_type, advocate_id=None):
        file = SimpleUploadedFile('scan.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        data = {'doc_type': doc_type, 'file': file}
        if advocate_id:
            data['advocate_id'] = advocate_id
        return self.client.post(f'/api/onboarding/applications/{app_id}/documents/', data, HTTP_X_APPLICANT_TOKEN=token)

    def create_and_submit(self):
        created = self.post('onboarding/applications/', payload()).json()
        app_id, token = created['application']['id'], created['token']
        # incomplete: documents are missing
        res = self.post(f'onboarding/applications/{app_id}/submit/', applicant=token)
        self.assertEqual(res.status_code, 400)
        self.assertTrue(any('Upload' in p for p in res.json()['problems']))
        for t in REQUIRED_FIRM_DOCS:
            self.assertEqual(self.upload(app_id, token, t).status_code, 201)
        adv_id = created['application']['advocates'][0]['id']
        self.upload(app_id, token, DocType.PRACTISING_CERTIFICATE, adv_id)
        res = self.post(f'onboarding/applications/{app_id}/submit/', applicant=token)
        self.assertEqual(res.status_code, 200, res.content)
        return app_id, token, res.json()

    def admin_post(self, path, data=None):
        return self.post(f'onboarding/admin/{path}', data, self.admin)

    def upload_agreement(self, app_id, agreement_type):
        file = SimpleUploadedFile('signed.pdf', b'%PDF-1.4 signed', content_type='application/pdf')
        return self.client.post(f'/api/onboarding/admin/applications/{app_id}/agreements/{agreement_type}/upload/', {'file': file},
                                HTTP_AUTHORIZATION=f'Bearer {self.admin}')

    def review_all_documents(self, app_id):
        for d in self.get(f'onboarding/admin/applications/{app_id}/', self.admin).json()['documents']:
            res = self.admin_post(f"applications/{app_id}/documents/{d['id']}/review/", {'decision': 'accept'})
            self.assertEqual(res.status_code, 200, res.content)

    # -- tests
    def test_public_endpoints_are_guarded(self):
        self.assertEqual(self.get('onboarding/options/').json()['max_primary_areas'], 5)
        created = self.post('onboarding/applications/', payload()).json()
        app_id = created['application']['id']
        self.assertEqual(self.get(f'onboarding/applications/{app_id}/').status_code, 403)  # no token
        self.assertEqual(self.get(f'onboarding/applications/{app_id}/', applicant='wrong').status_code, 403)
        self.assertEqual(self.get(f'onboarding/applications/{app_id}/', applicant=created['token']).status_code, 200)
        self.assertEqual(self.get('onboarding/admin/applications/').status_code, 401)  # admin list needs a login

    def test_admin_can_create_a_firm_without_partners_or_advocates(self):
        bare = payload(partners=[], advocates=[])
        self.assertEqual(self.post('onboarding/admin/applications/create/', bare).status_code, 401)  # needs an admin login
        # a firm applying for itself still has to list partners and an advocate
        own = self.post('onboarding/applications/', bare).json()
        for t in REQUIRED_FIRM_DOCS:
            self.upload(own['application']['id'], own['token'], t)
        res = self.post(f"onboarding/applications/{own['application']['id']}/submit/", applicant=own['token'])
        self.assertEqual(res.status_code, 400)
        self.assertTrue(any('partner' in p for p in res.json()['problems']) and any('advocate' in p for p in res.json()['problems']))

        created = self.post('onboarding/admin/applications/create/', bare, self.admin)
        self.assertEqual(created.status_code, 201, created.content)
        app_id, token = created.json()['application']['id'], created.json()['token']
        res = self.post(f'onboarding/applications/{app_id}/submit/', applicant=token)  # no documents uploaded: not needed
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], AppStatus.SUBMITTED)
        # a partner who is marked as the representative still needs an email
        again = self.post('onboarding/admin/applications/create/', payload(partners=[{'full_name': 'Juma', 'is_authorized_rep': True}], advocates=[]), self.admin).json()
        res = self.post(f"onboarding/applications/{again['application']['id']}/submit/", applicant=again['token'])
        self.assertEqual(res.status_code, 400)

    def test_firm_added_by_an_admin_is_approved_on_submit(self):
        created = self.post('onboarding/admin/applications/create/', payload(), self.admin).json()
        app_id, token = created['application']['id'], created['token']
        url = f'onboarding/admin/applications/{app_id}/submit-approved/'
        self.assertEqual(self.post(url, applicant=token).status_code, 401)  # needs an admin login
        res = self.post(url, token=self.admin, applicant=token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], AppStatus.APPROVED_FOR_TRIAL)
        app = FirmApplication.objects.get(id=app_id)
        self.assertEqual(app.organization.status, OrgStatus.APPROVED)
        self.assertFalse(app.documents.exclude(status='accepted').exists())
        self.assertTrue(User.objects.filter(email='neema@pnj.co.tz').exists())  # the listed advocate got an account
        # a firm's own application that is already in review cannot use this shortcut
        app_id2, token2, _ = self.create_and_submit()
        self.assertEqual(self.post(f'onboarding/admin/applications/{app_id2}/submit-approved/', token=self.admin, applicant=token2).status_code, 403)

    def test_validation(self):
        too_many = [{'code': c, 'is_primary': True} for c in ['mining', 'tax', 'family', 'adr', 'banking', 'ip']]
        self.assertEqual(self.post('onboarding/applications/', payload(practice_areas=too_many)).status_code, 400)
        self.assertEqual(self.post('onboarding/applications/', payload(phone_e164='abc')).status_code, 400)
        two_reps = payload(partners=[{'full_name': 'A', 'is_authorized_rep': True}, {'full_name': 'B', 'is_authorized_rep': True}])
        self.assertEqual(self.post('onboarding/applications/', two_reps).status_code, 400)
        self.assertEqual(self.post('onboarding/applications/', payload(phone_e164='0712440120')).json()['application']['phone_e164'], '+255712440120')

    def test_upload_rules(self):
        created = self.post('onboarding/applications/', payload()).json()
        app_id, token = created['application']['id'], created['token']
        bad = SimpleUploadedFile('virus.exe', b'x', content_type='application/octet-stream')
        res = self.client.post(f'/api/onboarding/applications/{app_id}/documents/', {'doc_type': 'tin_certificate', 'file': bad},
                               HTTP_X_APPLICANT_TOKEN=token)
        self.assertEqual(res.status_code, 400)

    def test_full_workflow_to_trial(self):
        app_id, token, submitted = self.create_and_submit()
        self.assertEqual(submitted['status'], AppStatus.SUBMITTED)
        self.assertRegex(submitted['application_number'], r'^CLC-LF/\d{4}/0001$')
        org = Organization.objects.get(application__id=app_id)
        self.assertEqual(org.status, OrgStatus.PENDING)  # no access before approval
        self.assertTrue(Notification.objects.filter(entity_id=app_id, channel='in_app').exists())
        self.assertEqual(self.post(f'onboarding/applications/{app_id}/submit/', applicant=token).status_code, 409)
        self.assertEqual(self.get(f'onboarding/applications/{app_id}/', applicant=token).json()['editable'], False)

        # review
        self.assertEqual(self.admin_post(f'applications/{app_id}/approve/').status_code, 409)  # too early
        self.assertEqual(self.admin_post(f'applications/{app_id}/start-review/').status_code, 200)
        self.assertEqual(self.admin_post(f'applications/{app_id}/complete-review/').status_code, 409)  # docs unreviewed
        self.review_all_documents(app_id)
        res = self.admin_post(f'applications/{app_id}/complete-review/')
        self.assertEqual(res.json()['status'], AppStatus.MEETING_PENDING)

        # approval is blocked until the meeting and the agreements are done
        self.assertEqual(self.admin_post(f'applications/{app_id}/approve/').status_code, 409)
        when = (timezone.now() + timedelta(days=2)).isoformat()
        res = self.admin_post(f'applications/{app_id}/meetings/', {'scheduled_at': when, 'meeting_type': 'online',
                                                                   'location_or_link': 'https://meet.example/clc'})
        meeting_id = res.json()['meetings'][0]['id']
        res = self.client.patch(f'/api/onboarding/admin/applications/{app_id}/meetings/{meeting_id}/',
                                {'outcome': 'completed', 'notes': 'All good', 'topics': ['confidentiality']},
                                content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {self.admin}')
        self.assertEqual(res.json()['status'], AppStatus.AGREEMENTS_PENDING)
        blocked = self.admin_post(f'applications/{app_id}/approve/')
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(len([p for p in blocked.json()['problems'] if 'signed copy not uploaded' in p]), 4)

        for t in ('engagement', 'code_of_conduct', 'nda', 'financial_terms'):
            self.assertEqual(self.upload_agreement(app_id, t).status_code, 200)
        mail.outbox.clear()
        res = self.admin_post(f'applications/{app_id}/approve/', {'trial_start': '2026-11-30'})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['status'], AppStatus.APPROVED_FOR_TRIAL)
        # trial start, end and billing date are stored separately; billing defaults to the end of the trial
        self.assertEqual((body['trial']['start'], body['trial']['end'], body['trial']['billing_effective']),
                         ('2026-11-30', '2027-01-30', '2027-01-30'))
        org.refresh_from_db()
        self.assertEqual(org.status, OrgStatus.APPROVED)

        # only the advocate's account is created automatically; the firm admin is created by CLC on the Users page
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Temporary password', mail.outbox[0].body)
        self.assertFalse(Membership.objects.filter(role='firm_admin', organization=org).exists())
        self.assertFalse(User.objects.filter(email='juma@pnj.co.tz').exists())
        profile = AdvocateProfile.objects.get(roll_number='ROLL-100')
        self.assertIsNotNone(profile.verified_at)

        # first login: must choose a new password before picking a role
        temp = mail.outbox[0].body.split('Temporary password: ')[1].split('\n')[0]
        login = self.post('auth/login/', {'email': 'neema@pnj.co.tz', 'password': temp}).json()
        self.assertTrue(login['must_change_password'])
        role = login['memberships'][0]['id']
        self.assertEqual(self.post('auth/select-role/', {'membership_id': role}, login['pick_role_token']).status_code, 403)
        self.assertEqual(self.post('auth/change-password/', {'current_password': temp, 'new_password': '12345678'},
                                   login['pick_role_token']).status_code, 400)  # too weak
        self.assertEqual(self.post('auth/change-password/', {'current_password': temp, 'new_password': 'Strong-Pass-2026!'},
                                   login['pick_role_token']).status_code, 204)
        self.assertEqual(self.post('auth/select-role/', {'membership_id': role}, login['pick_role_token']).status_code, 200)

        # audit trail
        actions = set(ActivityEvent.objects.filter(entity_id=app_id).values_list('action', flat=True))
        self.assertTrue({'application.submitted', 'application.review_started', 'meeting.scheduled', 'agreement.uploaded',
                         'application.approved', 'document.accept'} <= actions)

        # a firm in trial can later become active, or be suspended and reinstated
        self.assertEqual(self.admin_post(f'applications/{app_id}/suspend/', {'reason': ''}).status_code, 400)
        self.assertEqual(self.admin_post(f'applications/{app_id}/suspend/', {'reason': 'Complaint'}).json()['status'], 'suspended')
        org.refresh_from_db()
        self.assertEqual(org.status, OrgStatus.SUSPENDED)
        self.assertEqual(self.admin_post(f'applications/{app_id}/reinstate/').json()['status'], 'approved_for_trial')
        self.assertEqual(self.admin_post(f'applications/{app_id}/activate/').json()['status'], 'active')

    def test_correction_cycle(self):
        app_id, token, _ = self.create_and_submit()
        self.admin_post(f'applications/{app_id}/start-review/')
        doc = self.get(f'onboarding/admin/applications/{app_id}/', self.admin).json()['documents'][0]
        # replacement needs a reason
        self.assertEqual(self.admin_post(f"applications/{app_id}/documents/{doc['id']}/review/",
                                         {'decision': 'request_replacement'}).status_code, 400)
        self.admin_post(f"applications/{app_id}/documents/{doc['id']}/review/",
                        {'decision': 'request_replacement', 'note': 'Scan is unreadable'})
        res = self.admin_post(f'applications/{app_id}/requests/', {'kind': 'correction', 'message': 'TIN looks wrong'})
        self.assertEqual(res.json()['status'], AppStatus.CORRECTION_REQUIRED)
        self.assertTrue(any('TIN looks wrong' in m.body for m in mail.outbox))

        # the firm can edit again, replace the document and resubmit
        view = self.get(f'onboarding/applications/{app_id}/', applicant=token).json()
        self.assertTrue(view['editable'])
        self.assertEqual(view['requests'][0]['message'], 'TIN looks wrong')
        self.assertEqual(self.upload(app_id, token, doc['doc_type']).status_code, 201)
        put = self.client.put(f'/api/onboarding/applications/{app_id}/', {'tin': '999-999-999'}, content_type='application/json',
                              HTTP_X_APPLICANT_TOKEN=token)
        self.assertEqual(put.json()['tin'], '999-999-999')
        res = self.post(f'onboarding/applications/{app_id}/submit/', applicant=token)
        self.assertEqual(res.json()['status'], AppStatus.UNDER_REVIEW)
        self.assertIsNotNone(FirmApplication.objects.get(id=app_id).requests.first().resolved_at)  # request closed on resubmit

    def test_reject_needs_reason(self):
        app_id, _, _ = self.create_and_submit()
        self.assertEqual(self.admin_post(f'applications/{app_id}/reject/', {'reason': ' '}).status_code, 400)
        res = self.admin_post(f'applications/{app_id}/reject/', {'reason': 'Not registered with BRELA'})
        self.assertEqual(res.json()['status'], AppStatus.REJECTED)
        self.assertEqual(Organization.objects.get(application__id=app_id).status, OrgStatus.REJECTED)

    def test_compliance_alerts_and_helpers(self):
        app_id, _, _ = self.create_and_submit()
        self.admin_post(f'applications/{app_id}/start-review/')
        self.review_all_documents(app_id)
        self.admin_post(f'applications/{app_id}/complete-review/')
        when = (timezone.now() + timedelta(days=1)).isoformat()
        mid = self.admin_post(f'applications/{app_id}/meetings/', {'scheduled_at': when, 'meeting_type': 'physical'}).json()['meetings'][0]['id']
        self.client.patch(f'/api/onboarding/admin/applications/{app_id}/meetings/{mid}/', {'outcome': 'completed'},
                          content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {self.admin}')
        for t in ('engagement', 'code_of_conduct', 'nda', 'financial_terms'):
            self.upload_agreement(app_id, t)
        self.admin_post(f'applications/{app_id}/approve/')

        items = expiring_items()  # the advocate's certificate expires in 30 days
        self.assertEqual([(i['kind'], i['name'], i['expired']) for i in items], [('practising_certificate', 'Neema Lyimo', False)])
        self.assertEqual(self.get('onboarding/admin/compliance/', self.admin).json()[0]['days_left'], 30)

        from django.core.management import call_command
        call_command('check_expiries')
        self.assertTrue(Notification.objects.filter(subject__contains='expires in 30 days').exists())
        call_command('check_expiries')  # not repeated within a week
        self.assertEqual(Notification.objects.filter(subject__contains='expires in 30 days').count(), 1)

    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(add_months(date(2026, 12, 31), 2), date(2027, 2, 28))
        self.assertEqual(add_months(date(2026, 10, 2), 2), date(2026, 12, 2))
