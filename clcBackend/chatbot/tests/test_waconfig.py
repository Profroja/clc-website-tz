"""The WhatsApp connections an admin adds in the portal and chooses from."""
import hashlib
import hmac
from unittest import mock

from django.test import TestCase, override_settings

from chatbot import waconfig, whatsapp
from chatbot.models import WhatsAppConnection

from .helpers import make_org_users, sign_in


class WhatsAppConnectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_org_users()

    def setUp(self):
        self.token = sign_in(self.client, 'admin@clc.tz', 'S3cure-pass!', 'clc_admin')

    def api(self, method, url, data=None, token=None):
        return getattr(self.client, method)(f'/api/chatbot/{url}', data, content_type='application/json',
                                            headers={'Authorization': f'Bearer {token or self.token}'})

    def add(self, name='Main number', number='1283801101493291', token='EAAPyq-SECRET-TOKEN-1234', **more):
        return self.api('post', 'whatsapp/', {'name': name, 'phone_number_id': number, 'access_token': token, **more})

    def test_only_clc_admin(self):
        self.assertEqual(self.client.get('/api/chatbot/whatsapp/').status_code, 401)
        firm = sign_in(self.client, 'admin@clc.tz', 'S3cure-pass!', 'firm_admin')
        self.assertEqual(self.api('get', 'whatsapp/', token=firm).status_code, 403)

    @override_settings(WHATSAPP_PHONE_NUMBER_ID='', WHATSAPP_TOKEN='', WHATSAPP_VERIFY_TOKEN='', META_APP_SECRET='')
    def test_add_list_select_and_secrets_stay_hidden(self):
        self.assertEqual(self.api('get', 'whatsapp/').json()['connections'], [])
        res = self.add(app_secret='app-secret-9876', verify_token='clc-verify')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertNotIn('SECRET-TOKEN', str(res.json()))
        self.assertNotIn('app-secret-9876', str(res.json()))
        first = res.json()['connections'][0]
        self.assertEqual((first['token_hint'], first['in_use']), ('••••1234', False))  # added, not chosen yet
        self.assertEqual(waconfig.get('access_token'), '')

        second = self.add(name='Test number', number='555', token='tok-two-5555').json()['connections'][1]
        data = self.api('post', f"whatsapp/{first['id']}/use/").json()
        self.assertEqual([c['in_use'] for c in data['connections']], [True, False])
        self.assertEqual(waconfig.get('phone_number_id'), '1283801101493291')
        data = self.api('post', f"whatsapp/{second['id']}/use/").json()  # switching moves the "in use" mark
        self.assertEqual([c['in_use'] for c in data['connections']], [False, True])
        self.assertEqual(waconfig.get('access_token'), 'tok-two-5555')

        self.assertEqual(self.api('delete', f"whatsapp/{second['id']}/").status_code, 409)  # in use
        self.assertEqual(self.api('delete', f"whatsapp/{first['id']}/").status_code, 200)
        self.assertEqual(self.api('patch', f"whatsapp/{second['id']}/", {'name': 'Renamed', 'access_token': ''}).status_code, 200)
        self.assertEqual((WhatsAppConnection.objects.get().name, waconfig.get('access_token')), ('Renamed', 'tok-two-5555'))  # blank keeps the token

    def test_validation(self):
        self.assertEqual(self.add(number='abc').status_code, 400)
        self.assertEqual(self.add(name=' ').status_code, 400)
        self.assertEqual(self.add(token='').status_code, 400)
        self.assertEqual(self.add(api_version='23').status_code, 400)

    @override_settings(WHATSAPP_PHONE_NUMBER_ID='111', WHATSAPP_TOKEN='env-token', WHATSAPP_VERIFY_TOKEN='', META_APP_SECRET='')
    def test_env_is_the_fallback_and_a_chosen_connection_wins(self):
        env = self.api('get', 'whatsapp/').json()['env']
        self.assertEqual((env['available'], env['in_use']), (True, True))
        conn = self.add(number='222', token='conn-token').json()['connections'][0]
        self.api('post', f"whatsapp/{conn['id']}/use/")
        self.assertEqual(self.api('get', 'whatsapp/').json()['env']['in_use'], False)
        sent = {}

        def fake(method, url, **kw):
            sent.update(url=url, auth=kw['headers']['Authorization'])
            return mock.Mock(ok=True, json=lambda: {'messages': [{'id': 'wamid.X'}]})
        with mock.patch('chatbot.whatsapp.requests.request', fake):
            whatsapp.send('255700000000', {'type': 'text', 'text': {'body': 'hi'}})
        self.assertIn('/222/messages', sent['url'])
        self.assertEqual(sent['auth'], 'Bearer conn-token')
        self.api('post', 'whatsapp/use-env/')  # back to the .env values
        self.assertEqual(waconfig.get('phone_number_id'), '111')

    @override_settings(WHATSAPP_VERIFY_TOKEN='', META_APP_SECRET='')
    def test_webhook_accepts_any_saved_connection(self):
        self.add(verify_token='hook-one', app_secret='secret-one')
        self.add(name='Second', number='9', token='t2', verify_token='hook-two', app_secret='secret-two')
        ok = self.client.get('/api/whatsapp/webhook/', {'hub.mode': 'subscribe', 'hub.verify_token': 'hook-two', 'hub.challenge': 'abc'})
        self.assertEqual((ok.status_code, ok.content), (200, b'abc'))
        self.assertEqual(self.client.get('/api/whatsapp/webhook/', {'hub.mode': 'subscribe', 'hub.verify_token': 'nope'}).status_code, 403)
        body = b'{"object":"whatsapp_business_account","entry":[]}'
        sig = 'sha256=' + hmac.new(b'secret-two', body, hashlib.sha256).hexdigest()
        self.assertEqual(self.client.post('/api/whatsapp/webhook/', body, content_type='application/json',
                                          headers={'X-Hub-Signature-256': sig}).status_code, 200)
        self.assertEqual(self.client.post('/api/whatsapp/webhook/', body, content_type='application/json',
                                          headers={'X-Hub-Signature-256': 'sha256=bad'}).status_code, 401)

    def test_connection_test(self):
        conn = self.add(number='222', token='tok').json()['connections'][0]
        ok = mock.Mock(ok=True, json=lambda: {'display_phone_number': '+1 555 642 5281', 'verified_name': 'Test Number'})
        with mock.patch('chatbot.whatsapp.requests.request', return_value=ok):
            res = self.api('post', f"whatsapp/{conn['id']}/test/").json()
        self.assertEqual((res['ok'], res['display_phone_number']), (True, '+1 555 642 5281'))
        bad = mock.Mock(ok=False, status_code=401, json=lambda: {'error': {'message': 'Invalid OAuth access token'}})
        with mock.patch('chatbot.whatsapp.requests.request', return_value=bad):
            res = self.api('post', f"whatsapp/{conn['id']}/test/").json()
        self.assertFalse(res['ok'])
        self.assertIn('Invalid OAuth access token', res['detail'])
