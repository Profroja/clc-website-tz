"""The builder's Test chat in "real" mode: matters are really created and the chat is kept for the inbox."""
from django.core import mail
from django.test import TestCase

from cases.models import Case
from chatbot.starter import intake_flow
from clients.models import Client
from conversations.models import Conversation, ConversationStatus

from .helpers import make_org_users, sign_in


class RealTestModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_org_users()

    def setUp(self):
        self.token = sign_in(self.client, 'admin@clc.tz', 'S3cure-pass!', 'clc_admin')
        res = self.client.post('/api/chatbot/flows/', {'name': 'Real test'}, content_type='application/json',
                               headers={'Authorization': f'Bearer {self.token}'})
        self.flow_id = res.json()['id']

    def sim(self, state=None, event=None, real=True, label=None):
        body = {'definition': intake_flow(), 'lang': 'en', 'real': real, 'state': state, 'event': event, 'label': label}
        res = self.client.post(f'/api/chatbot/flows/{self.flow_id}/simulate/', body, content_type='application/json',
                               headers={'Authorization': f'Bearer {self.token}'})
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def play_to_the_end(self, real=True):
        turn = self.sim(real=real)
        for event, label in [({'kind': 'choice', 'choice_id': 'lang_en'}, 'English'), ({'kind': 'choice', 'choice_id': 'svc_will'}, 'Write a will'),
                             ({'kind': 'text', 'text': 'Amina Test'}, None), ({'kind': 'text', 'text': 'Dodoma'}, None),
                             ({'kind': 'text', 'text': 'I want to write a will for my family land.'}, None),
                             ({'kind': 'text', 'text': 'No documents'}, None)]:
            turn = self.sim(turn['state'], event, real=real, label=label)
        return turn

    def test_normal_test_saves_nothing(self):
        turn = self.play_to_the_end(real=False)
        self.assertEqual(Case.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertTrue(any(e['effect'] == 'create_case' for e in turn['effects']))

    def test_real_test_creates_a_real_matter_and_keeps_the_chat(self):
        mail.outbox.clear()
        turn = self.play_to_the_end()
        self.assertTrue(turn['real'])
        self.assertEqual(len(turn['matters']), 1)
        case = Case.objects.get()
        self.assertEqual((case.reference, case.region, case.status), (turn['matters'][0], 'Dodoma', 'new'))
        self.assertEqual(case.client.wa_id, 'test-builder')  # a fixed test client, never a real person
        self.assertEqual(case.client.full_name, 'Amina Test')
        self.assertEqual(case.documents.count(), 0)

        conv = Conversation.objects.get()
        self.assertEqual((str(conv.id), conv.case_id, conv.status), (turn['conversation_id'], case.id, ConversationStatus.QUEUED))  # handover is real
        texts = list(conv.messages.order_by('created_at').values_list('direction', 'body'))
        self.assertIn(('inbound', 'English'), texts)  # what the "client" tapped is kept, not an internal id
        self.assertTrue(any(d == 'outbound' and body and case.reference in body for d, body in texts))
        self.assertEqual(len(mail.outbox), 0)  # staff are never emailed from a test

    def test_each_new_real_test_starts_a_fresh_conversation(self):
        self.sim()
        first = Conversation.objects.get()
        self.sim()
        self.assertEqual(Conversation.objects.count(), 2)
        first.refresh_from_db()
        self.assertEqual(first.status, ConversationStatus.CLOSED)
        self.assertEqual(Client.objects.filter(wa_id='test-builder').count(), 1)
