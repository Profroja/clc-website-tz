from datetime import date, timedelta

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from accounts.models import AdvocateProfile, Membership, Organization, User
from cases.models import Case, CaseAssignment
from clients.models import Client
from core.models import Service
from notifications.models import Notification


class AdminMattersTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser('boss@clc.tz', 'Pw-12345-xyz', full_name='Boss')  # also a CLC Admin
        self.firm = Organization.objects.create(type='law_firm', name='PNJ Legal', status='approved')
        self.rival = Organization.objects.create(type='law_firm', name='Rival LLP', status='approved')
        self.pending = Organization.objects.create(type='law_firm', name='Pending LLP', status='pending')
        self.neema = self.advocate('neema@pnj.co.tz', 'Neema Lyimo', self.firm, 'R-1')
        self.baraka = self.advocate('baraka@rival.co.tz', 'Baraka Mushi', self.rival, 'R-2')
        mail.outbox.clear()
        client = Client.objects.create(wa_id='255711111111', full_name='John Mushi', phone_e164='+255711111111')
        self.service = Service.objects.create(code='land', name_sw='Ardhi', name_en='Land / Property')
        self.case = Case.objects.create(reference='CLC/2026/0001', client=client, service=self.service, region='Geita', status='new',
                                        summary='Boundary dispute', answers={'region': 'Geita', 'empty': '', 'lang': 'en', 'client_name': 'x'})

    def advocate(self, email, name, firm, roll, verified=True, expires=90):
        user = User.objects.create_user(email, 'Pw-12345-xyz', full_name=name)
        m = Membership.objects.create(user=user, organization=firm, role='advocate', status='active')
        AdvocateProfile.objects.create(user=user, roll_number=roll, practising_certificate='PC-' + roll,
                                       certificate_expires_on=date.today() + timedelta(days=expires),
                                       verified_at=timezone.now() if verified else None)
        return m

    def auth(self):
        login = self.client.post('/api/auth/login/', {'email': 'boss@clc.tz', 'password': 'Pw-12345-xyz'}, content_type='application/json').json()
        token = self.client.post('/api/auth/select-role/', {'membership_id': login['memberships'][0]['id']}, content_type='application/json',
                                 HTTP_AUTHORIZATION=f"Bearer {login['pick_role_token']}").json()['access']
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def test_list_and_detail_show_the_whole_matter(self):
        self.assertEqual(self.client.get('/api/admin/matters/').status_code, 401)
        auth = self.auth()
        rows = self.client.get('/api/admin/matters/', **auth).json()
        self.assertEqual([(r['reference'], r['client_name'], r['client_phone'], r['status_label'], r['advocate']) for r in rows],
                         [('CLC/2026/0001', 'John Mushi', '+255711111111', 'New', None)])  # the admin sees the real name
        detail = self.client.get(f'/api/admin/matters/{self.case.id}/', **auth).json()
        self.assertEqual((detail['summary'], detail['answers']), ('Boundary dispute', [{'label': 'Region', 'value': 'Geita'}]))  # empty answers left out
        self.assertEqual(self.client.get(f'/api/admin/matters/{self.case.id}/', **auth).status_code, 200)

    def test_only_assignable_advocates_are_offered(self):
        self.advocate('unverified@pnj.co.tz', 'Not Verified', self.firm, 'R-3', verified=False)
        self.advocate('expired@pnj.co.tz', 'Expired Cert', self.firm, 'R-4', expires=-5)
        self.advocate('pending@p.co.tz', 'In Pending Firm', self.pending, 'R-5')
        names = [(a['name'], a['firm']) for a in self.client.get('/api/admin/advocates/', **self.auth()).json()]
        self.assertEqual(names, [('Neema Lyimo', 'PNJ Legal'), ('Baraka Mushi', 'Rival LLP')])

    def test_assigning_a_matter(self):
        auth = self.auth()
        url = f'/api/admin/matters/{self.case.id}/assign/'
        post = lambda body: self.client.post(url, body, content_type='application/json', **auth)
        self.assertEqual(post({'advocate_id': 'nope'}).status_code, 400)

        res = post({'advocate_id': str(self.neema.id)})
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual((data['status_label'], data['advocate'], data['firm']), ('Advocate assigned', 'Neema Lyimo', 'PNJ Legal'))
        self.case.refresh_from_db()
        self.assertEqual(self.case.current_firm_id, self.firm.id)
        self.assertEqual(CaseAssignment.objects.filter(case=self.case, ended_at__isnull=True).count(), 1)

        # the advocate is told, by email and in the portal, without the client's name
        note = next(m for m in mail.outbox if m.to == ['neema@pnj.co.tz'])
        self.assertIn('CLC/2026/0001', note.body)
        self.assertNotIn('Mushi', note.body)
        self.assertTrue(Notification.objects.filter(membership=self.neema, entity_id=self.case.id).exists())
        self.assertEqual(post({'advocate_id': str(self.neema.id)}).status_code, 409)  # already hers

        # reassigning ends the first assignment and moves the matter to the other firm
        again = post({'advocate_id': str(self.baraka.id)}).json()
        self.assertEqual((again['advocate'], again['firm']), ('Baraka Mushi', 'Rival LLP'))
        self.assertEqual(CaseAssignment.objects.filter(case=self.case).count(), 2)
        self.assertEqual(CaseAssignment.objects.filter(case=self.case, ended_at__isnull=True).get().advocate_id, self.baraka.id)
        detail = self.client.get(f'/api/admin/matters/{self.case.id}/', **auth).json()
        self.assertEqual(len(detail['history']), 2)
        self.assertIn('Assigned to Baraka Mushi', detail['history'][-1]['note'])

    def test_assigned_matter_appears_for_the_advocate_and_closed_ones_are_refused(self):
        auth = self.auth()
        self.client.post(f'/api/admin/matters/{self.case.id}/assign/', {'advocate_id': str(self.neema.id)}, content_type='application/json', **auth)
        login = self.client.post('/api/auth/login/', {'email': 'neema@pnj.co.tz', 'password': 'Pw-12345-xyz'}, content_type='application/json').json()
        token = self.client.post('/api/auth/select-role/', {'membership_id': login['memberships'][0]['id']}, content_type='application/json',
                                 HTTP_AUTHORIZATION=f"Bearer {login['pick_role_token']}").json()['access']
        mine = self.client.get('/api/advocate/matters/', HTTP_AUTHORIZATION=f'Bearer {token}').json()
        self.assertEqual([(m['reference'], m['client'], m['status_label']) for m in mine], [('CLC/2026/0001', 'John Mushi', 'New')])
        self.case.status, self.case.outcome, self.case.closed_at = 'closed', 'resolved', timezone.now()
        self.case.save()
        res = self.client.post(f'/api/admin/matters/{self.case.id}/assign/', {'advocate_id': str(self.baraka.id)}, content_type='application/json', **auth)
        self.assertEqual(res.status_code, 409)

    def test_delete_a_matter_permanently(self):
        auth = self.auth()
        self.client.post(f'/api/admin/matters/{self.case.id}/assign/', {'advocate_id': str(self.neema.id)}, content_type='application/json', **auth)
        from cases.models import CasePayment
        CasePayment.objects.create(case=self.case, recorded_by=self.neema, purpose='Fee', amount=1000, paid_on=date.today())
        detail = self.client.get(f'/api/admin/matters/{self.case.id}/', **auth).json()
        self.assertEqual(detail['payments']['totals'], [{'currency': 'TZS', 'amount': '1000.00'}])  # the admin sees what was recorded
        self.assertEqual(self.client.delete(f'/api/admin/matters/{self.case.id}/delete/').status_code, 401)
        res = self.client.delete(f'/api/admin/matters/{self.case.id}/delete/', **auth)
        self.assertEqual((res.status_code, res.json()), (200, {'deleted': 'CLC/2026/0001'}))
        self.assertFalse(Case.objects.exists())
        self.assertFalse(CaseAssignment.objects.exists())
        self.assertEqual(self.client.get('/api/admin/matters/', **auth).json(), [])
        self.assertEqual(self.client.delete(f'/api/admin/matters/{self.case.id}/delete/', **auth).status_code, 404)

    def test_ready_to_close_shows_in_the_status(self):
        auth = self.auth()
        self.case.current_firm = self.firm
        self.case.status, self.case.ready_to_close_at, self.case.ready_to_close_summary = 'active', timezone.now(), 'Done'
        self.case.save()
        row = self.client.get('/api/admin/matters/', **auth).json()[0]
        self.assertEqual((row['status_label'], row['ready_to_close']), ('Ready to close', True))

    def test_admin_closes_a_matter_that_is_ready(self):
        auth = self.auth()
        url = f'/api/admin/matters/{self.case.id}/close/'
        self.client.post(f'/api/admin/matters/{self.case.id}/assign/', {'advocate_id': str(self.neema.id)}, content_type='application/json', **auth)
        self.assertEqual(self.client.post(url, {}, content_type='application/json', **auth).status_code, 409)  # not ready yet
        self.case.refresh_from_db()
        self.case.status, self.case.ready_to_close_at, self.case.ready_to_close_summary = 'active', timezone.now(), 'Done'
        self.case.save()
        res = self.client.post(url, {'comment': 'Well handled'}, content_type='application/json', **auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['status'], res.json()['status_label']), ('closed', 'Closed'))
        self.case.refresh_from_db()
        self.assertEqual((self.case.outcome, self.case.close_reason, self.case.closed_by.user.full_name), ('resolved', 'Well handled', 'Boss'))
        self.assertEqual(self.client.post(url, {}, content_type='application/json', **auth).status_code, 409)  # already closed
        self.assertTrue(Notification.objects.filter(membership=self.neema, subject='A matter you handled was closed').exists())

    def test_unassigned_filter(self):
        auth = self.auth()
        self.assertEqual(len(self.client.get('/api/admin/matters/?unassigned=1', **auth).json()), 1)
        self.client.post(f'/api/admin/matters/{self.case.id}/assign/', {'advocate_id': str(self.neema.id)}, content_type='application/json', **auth)
        self.assertEqual(self.client.get('/api/admin/matters/?unassigned=1', **auth).json(), [])
