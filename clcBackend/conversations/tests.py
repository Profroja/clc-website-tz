from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from cases.models import Case
from chatbot import whatsapp
from clients.models import Client
from conversations.models import BotFlow, BotFlowVersion, BotSession, Conversation, ConversationStatus, Message


class InboxTests(TestCase):
    def setUp(self):
        User.objects.create_superuser('boss@clc.tz', 'Pw-12345-xyz', full_name='Boss Admin')
        login = self.client.post('/api/auth/login/', {'email': 'boss@clc.tz', 'password': 'Pw-12345-xyz'}, content_type='application/json').json()
        token = self.client.post('/api/auth/select-role/', {'membership_id': login['memberships'][0]['id']}, content_type='application/json',
                                 HTTP_AUTHORIZATION=f"Bearer {login['pick_role_token']}").json()['access']
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        self.amina = Client.objects.create(wa_id='255711111111', phone_e164='+255711111111', full_name='Amina Said')
        self.baraka = Client.objects.create(wa_id='255722222222', phone_e164='+255722222222', full_name='Baraka Ali')
        self.conv = Conversation.objects.create(client=self.amina, status=ConversationStatus.QUEUED, last_inbound_at=timezone.now())
        self.bot_conv = Conversation.objects.create(client=self.baraka, status=ConversationStatus.BOT, last_inbound_at=timezone.now())
        for conv, rows in ((self.conv, [('inbound', 'client', 'Habari, naomba msaada'), ('outbound', 'bot', 'Karibu CLC')]),
                           (self.bot_conv, [('inbound', 'client', 'Mambo')])):
            for i, (d, s, b) in enumerate(rows):
                Message.objects.create(conversation=conv, direction=d, sender_type=s, body=b, status='received' if d == 'inbound' else 'sent',
                                       created_at=timezone.now() - timedelta(minutes=10 - i))

    def get(self, path):
        return self.client.get(f'/api/admin/conversations/{path}', **self.auth)

    def post(self, path, body=None):
        return self.client.post(f'/api/admin/conversations/{path}', body or {}, content_type='application/json', **self.auth)

    def test_list_puts_waiting_clients_first(self):
        self.assertEqual(self.client.get('/api/admin/conversations/').status_code, 401)
        rows = self.get('').json()
        self.assertEqual([(r['client_name'], r['status']) for r in rows], [('Amina Said', 'queued'), ('Baraka Ali', 'bot')])
        self.assertEqual(rows[0]['last_message'], 'Karibu CLC')
        self.assertEqual(len(self.get('?status=bot').json()), 1)

    def test_thread_shows_the_whole_conversation(self):
        data = self.get(f'{self.conv.id}/').json()
        self.assertEqual([(m['sender'], m['body']) for m in data['messages']], [('client', 'Habari, naomba msaada'), ('bot', 'Karibu CLC')])
        self.assertTrue(data['window_open'])

    def test_take_over_stops_the_bot_and_release_gives_it_back(self):
        flow = BotFlow.objects.create(name='F')
        version = BotFlowVersion.objects.create(flow=flow, version=1, definition={'nodes': [], 'edges': []}, status='published')
        session = BotSession.objects.create(conversation=self.bot_conv, flow_version=version)
        taken = self.post(f'{self.bot_conv.id}/take-over/').json()
        self.assertEqual((taken['status'], taken['taken_over_by']), ('with_staff', 'Boss Admin (CLC Admin)'))
        session.refresh_from_db()
        self.assertEqual(session.end_reason, 'handoff')  # the half-finished bot flow is ended
        back = self.post(f'{self.bot_conv.id}/release/').json()
        self.assertEqual((back['status'], back['taken_over_by']), ('bot', None))
        self.assertEqual([(e['who'], e['text']) for e in back['events']],
                         [('Boss Admin (CLC Admin)', 'took over this chat'), ('Boss Admin (CLC Admin)', 'gave this chat back to the bot')])  # shown in the chat

    def test_staff_message_is_sent_on_whatsapp_and_takes_the_chat_over(self):
        with mock.patch.object(whatsapp, 'send', return_value='wamid.OUT1') as send:
            res = self.post(f'{self.conv.id}/send/', {'text': '  Habari Amina, tupo hapa.  '})
        self.assertEqual(res.status_code, 201, res.content)
        send.assert_called_once_with('255711111111', {'type': 'text', 'text': {'body': 'Habari Amina, tupo hapa.', 'preview_url': False}})
        msg = Message.objects.get(provider_message_id='wamid.OUT1')
        self.assertEqual((msg.sender_type, msg.status, msg.sent_as_membership.user.full_name), ('staff', 'sent', 'Boss Admin'))
        self.conv.refresh_from_db()
        self.assertEqual((self.conv.status, self.conv.taken_over_by.user.full_name), ('with_staff', 'Boss Admin'))
        self.assertEqual(res.json()['conversation']['messages'][-1]['staff'], 'Boss Admin (CLC Admin)')
        self.assertEqual([e['text'] for e in res.json()['conversation']['events']], ['took over this chat'])  # sending took it over

    def test_sending_is_refused_in_the_cases_whatsapp_refuses(self):
        self.assertEqual(self.post(f'{self.conv.id}/send/', {'text': '  '}).status_code, 400)
        with mock.patch.object(whatsapp, 'send') as send:
            self.conv.last_inbound_at = timezone.now() - timedelta(hours=25)  # outside WhatsApp's 24-hour window
            self.conv.save()
            late = self.post(f'{self.conv.id}/send/', {'text': 'Hello'})
            self.assertEqual((late.status_code, late.json()['code']), (409, 'window_closed'))
            send.assert_not_called()
        self.conv.last_inbound_at = timezone.now()
        self.conv.save()
        with mock.patch.object(whatsapp, 'send', side_effect=whatsapp.MetaError('Meta API 401: Invalid token')):
            failed = self.post(f'{self.conv.id}/send/', {'text': 'Hello'})
        self.assertEqual(failed.status_code, 502)
        self.assertIn('Invalid token', failed.json()['detail'])
        self.assertEqual(Message.objects.filter(status='failed').count(), 1)  # kept, marked failed, so the admin can see it
        closed = self.post(f'{self.bot_conv.id}/close/').json()
        self.assertEqual(closed['status'], 'closed')
        self.assertEqual(self.post(f'{self.bot_conv.id}/send/', {'text': 'Hi'}).status_code, 409)

    def test_builder_test_conversations_are_never_sent_to_whatsapp(self):
        tester = Client.objects.create(wa_id='test-builder', full_name='Test client (flow builder)')
        conv = Conversation.objects.create(client=tester, status=ConversationStatus.QUEUED)
        with mock.patch.object(whatsapp, 'send') as send:
            res = self.post(f'{conv.id}/send/', {'text': 'Hello tester'})
        self.assertEqual(res.status_code, 201)
        send.assert_not_called()
        self.assertTrue(self.get(f'{conv.id}/').json()['is_test'])

    def test_the_matter_is_linked(self):
        case = Case.objects.create(reference='CLC/2026/0001', client=self.amina, conversation=self.conv)
        self.conv.case = case
        self.conv.save()
        row = next(r for r in self.get('').json() if r['client_name'] == 'Amina Said')
        self.assertEqual((row['matter_reference'], row['matter_id']), ('CLC/2026/0001', str(case.id)))
