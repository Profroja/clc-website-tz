from unittest import mock

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import User


def fake_youtube(live_video=None):
    """Stands in for the three YouTube Data API calls."""
    def get(path, **params):
        if path == 'channels':
            return {'items': [{'contentDetails': {'relatedPlaylists': {'uploads': 'UU123'}}}]}
        if path == 'playlistItems':
            return {'items': [{'contentDetails': {'videoId': 'old00000001'}}, {'contentDetails': {'videoId': 'new00000002'}}]}
        return {'items': [
            {'id': 'old00000001', 'snippet': {'title': 'Old episode', 'liveBroadcastContent': 'none'}},
            {'id': 'new00000002', 'snippet': {'title': 'Haki za Wachimbaji LIVE', 'liveBroadcastContent': 'live' if live_video else 'none'}},
        ]}
    return get


class LiveTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_superuser('boss@clc.tz', 'Pw-12345-xyz', full_name='Boss')
        login = self.client.post('/api/auth/login/', {'email': 'boss@clc.tz', 'password': 'Pw-12345-xyz'}, content_type='application/json').json()
        token = self.client.post('/api/auth/select-role/', {'membership_id': login['memberships'][0]['id']}, content_type='application/json',
                                 HTTP_AUTHORIZATION=f"Bearer {login['pick_role_token']}").json()['access']
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def patch(self, body):
        return self.client.patch('/api/admin/live/', body, content_type='application/json', **self.auth)

    def test_offline_by_default(self):
        data = self.client.get('/api/live/').json()
        self.assertEqual((data['live'], data['video_id']), (False, None))
        self.assertEqual(data['channel_url'], 'https://www.youtube.com/@Communitylegalclinic')
        self.assertEqual(self.client.get('/api/admin/live/').status_code, 401)  # settings need an admin

    def test_whatsapp_number_is_cleaned_and_validated(self):
        ok = self.patch({'whatsapp_number': '+255 745 118 253', 'whatsapp_message': 'Habari'})
        self.assertEqual(ok.json()['whatsapp_number'], '255745118253')
        self.assertEqual(self.client.get('/api/live/').json()['whatsapp_number'], '255745118253')
        self.assertEqual(self.patch({'whatsapp_number': '0745118253'}).json()['whatsapp_number'], '255745118253')
        self.assertEqual(self.patch({'whatsapp_number': 'call me'}).status_code, 400)

    def test_manual_go_live(self):
        self.assertEqual(self.patch({'manual_live': True}).status_code, 400)  # needs the video link first
        res = self.patch({'manual_video_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'manual_live': True, 'popup_title': 'Podcast LIVE'})
        self.assertEqual(res.status_code, 200, res.content)
        data = self.client.get('/api/live/').json()
        self.assertEqual((data['live'], data['source'], data['video_id'], data['title']), (True, 'manual', 'dQw4w9WgXcQ', 'Podcast LIVE'))
        self.patch({'manual_live': False})
        self.assertFalse(self.client.get('/api/live/').json()['live'])
        self.assertEqual(self.patch({'manual_video_url': 'nonsense'}).status_code, 400)

    @override_settings(YOUTUBE_API_KEY='test-key')
    def test_automatic_detection(self):
        with mock.patch('core.live._get', fake_youtube(live_video=True)):
            data = self.client.get('/api/live/').json()
        self.assertEqual((data['live'], data['source'], data['video_id'], data['title']),
                         (True, 'auto', 'new00000002', 'Haki za Wachimbaji LIVE'))  # the YouTube title is used
        self.assertTrue(self.client.get('/api/admin/live/', **self.auth).json()['auto_detect'])

    @override_settings(YOUTUBE_API_KEY='test-key')
    def test_not_live_and_cached_and_errors(self):
        with mock.patch('core.live._get', side_effect=fake_youtube(live_video=False)) as get:
            self.assertFalse(self.client.get('/api/live/').json()['live'])
            self.client.get('/api/live/')
            self.assertEqual(get.call_count, 3)  # channels + playlistItems + videos, once: the answer is cached for a minute
        cache.clear()
        with mock.patch('core.live._get', side_effect=requests.ConnectionError('down')):
            self.assertFalse(self.client.get('/api/live/').json()['live'])  # a YouTube outage must not break the site
