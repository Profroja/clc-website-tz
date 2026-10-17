from datetime import date, timedelta

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.utils import timezone

import shutil
import tempfile

from accounts.models import AdvocateProfile, Membership, Organization, User
from appointments.models import Appointment
from cases.models import Case, CaseAssignment, Document
from clients.models import Client
from conversations.models import Conversation, Message
from core.models import Service
from onboarding.models import AppStatus, FirmApplication


class AdvocatePortalTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.firm = Organization.objects.create(type='law_firm', name='PNJ Legal', status='approved')
        FirmApplication.objects.create(organization=self.firm, token_hash='x', firm_name='PNJ Legal', status=AppStatus.APPROVED_FOR_TRIAL,
                                       trial_start=date.today() - timedelta(days=10), trial_end=date.today() + timedelta(days=50),
                                       billing_effective=date.today() + timedelta(days=50))
        self.neema = self.make_advocate('neema@pnj.co.tz', 'Neema Lyimo', 'R-1')
        self.other = self.make_advocate('baraka@pnj.co.tz', 'Baraka Mushi', 'R-2')
        self.admin = Membership.objects.create(user=User.objects.create_user('juma@pnj.co.tz', 'Pw-12345-xyz', full_name='Juma'),
                                               organization=self.firm, role='firm_admin', status='active')
        client = Client.objects.create(wa_id='255711111111', full_name='John Mushi')
        service = Service.objects.create(code='land', name_sw='Ardhi', name_en='Land / Property')
        convo = Conversation.objects.create(client=client, channel='whatsapp')
        Message.objects.create(conversation=convo, direction='inbound', sender_type='client', body='Habari, nina swali la ardhi', status='received')
        Message.objects.create(conversation=convo, direction='outbound', sender_type='bot', body='Karibu CLC', status='sent')
        self.new_case = self.make_case('CLC/2026/0012', client, service, 'advocate_assigned', self.neema, convo)
        self.active_case = self.make_case('CLC/2026/0017', client, service, 'active', self.neema)
        self.others_case = self.make_case('CLC/2026/0020', client, service, 'advocate_assigned', self.other)

    def make_advocate(self, email, name, roll):
        user = User.objects.create_user(email, 'Pw-12345-xyz', full_name=name, phone_e164='+255700000002')
        m = Membership.objects.create(user=user, organization=self.firm, role='advocate', status='active')
        AdvocateProfile.objects.create(user=user, roll_number=roll, practising_certificate='PC-' + roll,
                                       certificate_expires_on=date.today() + timedelta(days=90), verified_at=timezone.now())
        return m

    def make_case(self, ref, client, service, status, advocate, convo=None):
        case = Case.objects.create(reference=ref, client=client, service=service, region='Geita', status=status, priority='high',
                                   current_firm=self.firm, conversation=convo, summary='Boundary dispute')
        CaseAssignment.objects.create(case=case, advocate=advocate, assigned_by=self.admin)
        return case

    def token(self, email='neema@pnj.co.tz'):
        login = self.client.post('/api/auth/login/', {'email': email, 'password': 'Pw-12345-xyz'}, content_type='application/json').json()
        return self.client.post('/api/auth/select-role/', {'membership_id': login['memberships'][0]['id']},
                                content_type='application/json', HTTP_AUTHORIZATION=f"Bearer {login['pick_role_token']}").json()['access']

    def api(self, method, path, body=None, email='neema@pnj.co.tz'):
        kw = {'HTTP_AUTHORIZATION': f'Bearer {self.token(email)}'}
        if method == 'get':
            return self.client.get(f'/api/advocate/{path}', **kw)
        return getattr(self.client, method)(f'/api/advocate/{path}', body or {}, content_type='application/json', **kw)

    # --- dashboard
    def test_dashboard_matches_the_design(self):
        data = self.api('get', 'overview/').json()
        self.assertEqual(data['stats'], {'assigned': 1, 'in_progress': 1, 'closed': 0})
        refs = [(m['reference'], m['service'], m['client'], m['status_label']) for m in data['matters']]
        self.assertEqual(sorted(refs), [('CLC/2026/0012', 'Land / Property', 'John Mushi', 'New'),
                                        ('CLC/2026/0017', 'Land / Property', 'John Mushi', 'In progress')])
        self.assertEqual(data['details']['roll_number'], 'R-1')
        self.assertEqual(data['name'], 'Neema Lyimo')

    def test_subscription_page(self):
        data = self.api('get', 'subscription/').json()
        self.assertEqual((data['law_firm'], data['subscription']['trial_days_left'], data['subscription']['in_trial']), ('PNJ Legal', 50, True))

    def test_only_advocates_get_in(self):
        self.assertEqual(self.client.get('/api/advocate/overview/').status_code, 401)
        self.assertEqual(self.api('get', 'overview/', email='juma@pnj.co.tz').status_code, 403)

    # --- a matter is private to its advocate
    def test_other_advocates_matters_are_invisible(self):
        mine = self.api('get', 'matters/').json()
        self.assertEqual(sorted(m['reference'] for m in mine), ['CLC/2026/0012', 'CLC/2026/0017'])
        theirs = self.others_case.id
        for path in ('', 'messages/', 'documents/', 'appointments/', 'notes/'):
            self.assertEqual(self.api('get', f'matters/{theirs}/{path}').status_code, 404, path)
        self.assertEqual(self.api('post', f'matters/{theirs}/start/').status_code, 404)

    def test_matter_detail_and_messages(self):
        detail = self.api('get', f'matters/{self.new_case.id}/').json()
        self.assertEqual((detail['reference'], detail['client'], detail['summary']), ('CLC/2026/0012', 'John Mushi', 'Boundary dispute'))  # the advocate knows their client
        chat = self.api('get', f'matters/{self.new_case.id}/messages/').json()
        self.assertEqual([(m['sender'], m['body']) for m in chat['messages']], [('client', 'Habari, nina swali la ardhi'), ('bot', 'Karibu CLC')])
        self.assertNotIn('255711111111', str(chat))  # no phone number either
        self.assertFalse(chat['can_chat'])  # not started yet
        self.assertEqual(self.api('get', f'matters/{self.active_case.id}/messages/').json()['messages'], [])  # no conversation linked

    def test_advocate_takes_over_and_chats_after_starting_work(self):
        from unittest import mock

        from chatbot import whatsapp
        url = f'matters/{self.new_case.id}/chat/'
        self.assertEqual(self.api('post', url + 'take-over/').status_code, 409)  # work not started
        self.assertEqual(self.api('post', url + 'send/', {'text': 'Hello'}).status_code, 409)
        self.api('post', f'matters/{self.new_case.id}/start/')
        convo = self.new_case.conversation
        self.assertEqual(self.api('post', url + 'send/', {'text': 'Hello'}, email='baraka@pnj.co.tz').status_code, 404)  # not his matter
        self.assertEqual(self.api('post', url + 'send/', {'text': 'Hello'}).status_code, 409)  # client has not written within 24 hours

        convo.last_inbound_at = timezone.now()
        convo.save()
        taken = self.api('post', url + 'take-over/').json()
        self.assertEqual((taken['status'], taken['mine'], [e['text'] for e in taken['events']], taken['events'][0]['who']),
                         ('with_staff', True, ['took over this chat'], 'You'))
        with mock.patch.object(whatsapp, 'send', return_value='wamid.ADV1') as send:
            res = self.api('post', url + 'send/', {'text': ' Habari John, naitwa Neema. '})
        self.assertEqual(res.status_code, 201, res.content)
        send.assert_called_once_with('255711111111', {'type': 'text', 'text': {'body': 'Habari John, naitwa Neema.', 'preview_url': False}})
        last = res.json()['chat']['messages'][-1]
        self.assertEqual((last['body'], last['mine'], last['sender']), ('Habari John, naitwa Neema.', True, 'staff'))
        convo.refresh_from_db()
        self.assertEqual(convo.taken_over_by_id, self.neema.id)
        back = self.api('post', url + 'release/').json()
        self.assertEqual(back['status'], 'bot')

    # --- documents
    def test_documents_download(self):
        key = default_storage.save('case-docs/test.pdf', ContentFile(b'%PDF-1.4 hello'))
        doc = Document.objects.create(case=self.new_case, file_name='title-deed.pdf', mime_type='application/pdf', size_bytes=14,
                                      storage_key=key, sha256='0' * 64)
        hidden = Document.objects.create(case=self.new_case, file_name='clc-only.pdf', mime_type='application/pdf', size_bytes=1,
                                         storage_key='x/hidden.pdf', sha256='1' * 64, visibility='clc_only')
        listed = self.api('get', f'matters/{self.new_case.id}/documents/').json()
        self.assertEqual([d['file_name'] for d in listed], ['title-deed.pdf'])  # CLC-only files stay hidden
        got = self.api('get', f'matters/{self.new_case.id}/documents/{doc.id}/download/')
        self.assertEqual((got.status_code, b''.join(got.streaming_content)), (200, b'%PDF-1.4 hello'))
        self.assertEqual(self.api('get', f'matters/{self.new_case.id}/documents/{hidden.id}/download/').status_code, 404)
        self.assertEqual(self.api('get', f'matters/{self.active_case.id}/documents/{doc.id}/download/').status_code, 404)  # another matter

    # --- notes
    def test_notes_stay_inside_the_firm(self):
        url = f'matters/{self.new_case.id}/notes/'
        self.assertEqual(self.api('post', url, {'body': '  '}).status_code, 400)
        notes = self.api('post', url, {'body': 'Called the surveyor'}).json()
        self.assertEqual([(n['body'], n['author']) for n in notes], [('Called the surveyor', 'Neema Lyimo')])
        from cases.models import CaseNote
        rival = Organization.objects.create(type='law_firm', name='Rival LLP', status='approved')
        rival_member = Membership.objects.create(user=User.objects.create_user('r@rival.co.tz', 'Pw-12345-xyz', full_name='R'),
                                                 organization=rival, role='advocate', status='active')
        CaseNote.objects.create(case=self.new_case, organization=rival, author=rival_member, body='Secret of another firm')
        self.assertEqual([n['body'] for n in self.api('get', url).json()], ['Called the surveyor'])

    # --- status
    def test_start_then_ready_to_close(self):
        url = f'matters/{self.new_case.id}/'
        self.assertEqual(self.api('post', url + 'ready-to-close/', {'summary': 'x'}).status_code, 409)  # not started yet
        started = self.api('post', url + 'start/')
        self.assertEqual((started.status_code, started.json()['status_label']), (200, 'In progress'))
        self.assertEqual(self.api('post', url + 'start/').status_code, 409)
        self.assertEqual(self.api('post', url + 'ready-to-close/', {'summary': ' '}).status_code, 400)
        self.assertEqual(self.api('post', url + 'ready-to-close/', {'summary': 'Settled with the neighbour'}).status_code, 200)
        detail = self.api('get', url).json()
        self.assertEqual((detail['status_label'], detail['ready_to_close_summary']), ('Ready to close', 'Settled with the neighbour'))
        listed = [m['status_label'] for m in self.api('get', 'matters/').json() if m['id'] == str(self.new_case.id)]
        self.assertEqual(listed, ['Ready to close'])
        self.assertEqual([h['to'] for h in detail['history']], ['active', 'active'])
        self.assertEqual(detail['history'][0]['by'], 'Neema Lyimo')

    def test_advocate_records_payments(self):
        url = f'matters/{self.new_case.id}/payments/'
        self.assertEqual(self.api('get', url).json(), {'rows': [], 'totals': []})
        ok = {'purpose': 'Legal fee', 'amount': '150,000', 'currency': 'TZS', 'paid_on': str(date.today()), 'method': 'mobile_money', 'reference': 'MP123'}
        for bad in ({**ok, 'purpose': ' '}, {**ok, 'amount': '0'}, {**ok, 'amount': 'abc'}, {**ok, 'amount': '10.123'},
                    {**ok, 'paid_on': str(date.today() + timedelta(days=2))}, {**ok, 'method': 'gold'}, {**ok, 'currency': 'XYZ'}):
            self.assertEqual(self.api('post', url, bad).status_code, 400, bad)
        self.assertEqual(self.api('post', url, ok, email='baraka@pnj.co.tz').status_code, 404)  # not his matter
        res = self.api('post', url, ok)
        self.assertEqual(res.status_code, 200, res.content)
        self.api('post', url, {**ok, 'purpose': 'Court filing fee', 'amount': '25000.50'})
        data = self.api('get', url).json()
        self.assertEqual(len(data['rows']), 2)
        self.assertEqual(data['totals'], [{'currency': 'TZS', 'amount': '175000.50'}])
        self.assertEqual((data['rows'][0]['method'], data['rows'][0]['recorded_by']), ('Mobile money', 'Neema Lyimo'))
        self.new_case.status, self.new_case.outcome, self.new_case.closed_at = 'closed', 'resolved', timezone.now()
        self.new_case.save()
        self.assertEqual(self.api('post', url, ok).status_code, 409)  # closed matters take no more payments

    # --- appointments
    def test_assigned_appointments(self):
        from appointments.availability import TZ
        when = timezone.now() + timedelta(days=2)
        mine = Appointment.objects.create(reference='APT/2026/0001', service='Wills', meeting_kind='phone', scheduled_at=when, full_name='Visitor One',
                                          phone_e164='+255700111222', email='v@example.com', advocate=self.neema)
        Appointment.objects.create(reference='APT/2026/0002', service='Wills', meeting_kind='phone', scheduled_at=when + timedelta(hours=1),
                                   full_name='Not Mine', phone_e164='+255700111333', email='n@example.com', advocate=self.other)
        rows = self.api('get', 'appointments/').json()
        self.assertEqual([(r['reference'], r['full_name']) for r in rows], [('APT/2026/0001', 'Visitor One')])
        self.assertEqual(rows[0]['time'], when.astimezone(TZ).strftime('%H:%M'))
        self.assertEqual(len(self.api('get', f'matters/{self.new_case.id}/appointments/').json()), 1)
        self.assertEqual(mine.advocate_id, self.neema.id)
