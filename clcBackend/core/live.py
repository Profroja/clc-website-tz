"""Is CLC live on YouTube right now? Checked with the official YouTube Data API (needs YOUTUBE_API_KEY),
or set by hand in the admin portal."""
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from .models import LiveSettings

log = logging.getLogger('live')
API = 'https://www.googleapis.com/youtube/v3'
CHECK_SECONDS = 60  # one check a minute costs about 2 quota units each: far inside the free daily quota


def auto_detect_configured():
    return bool(settings.YOUTUBE_API_KEY)


def _get(path, **params):
    res = requests.get(f'{API}/{path}', params={**params, 'key': settings.YOUTUBE_API_KEY}, timeout=5)
    res.raise_for_status()
    return res.json()


def _uploads_playlist(handle):
    key = f'live:uploads:{handle}'
    playlist = cache.get(key)
    if playlist is None:
        items = _get('channels', part='contentDetails', forHandle=handle).get('items') or []
        playlist = items[0]['contentDetails']['relatedPlaylists']['uploads'] if items else ''
        cache.set(key, playlist, 24 * 3600 if playlist else 300)
    return playlist


def detect_from_youtube(handle):
    """Returns {'video_id', 'title'} of the channel's live stream, or None."""
    playlist = _uploads_playlist(handle)
    if not playlist:
        return None
    ids = [i['contentDetails']['videoId'] for i in
           _get('playlistItems', part='contentDetails', playlistId=playlist, maxResults=5).get('items', [])]
    if not ids:
        return None
    for v in _get('videos', part='snippet,liveStreamingDetails', id=','.join(ids)).get('items', []):
        if v['snippet'].get('liveBroadcastContent') == 'live':
            return {'video_id': v['id'], 'title': v['snippet']['title']}
    return None


def current_live():
    """What the website needs: whether we are live, the video, and the WhatsApp number for the live chat."""
    s = LiveSettings.load()
    live, source = None, None
    if s.manual_live and s.manual_video_id:
        live, source = {'video_id': s.manual_video_id, 'title': ''}, 'manual'
    elif auto_detect_configured():
        key = f'live:status:{s.channel_handle}'
        found = cache.get(key, 'miss')
        if found == 'miss':
            try:
                found = detect_from_youtube(s.channel_handle)
            except (requests.RequestException, KeyError, ValueError):
                log.exception('YouTube live check failed')
                found = None
            cache.set(key, found, CHECK_SECONDS)
        if found:
            live, source = found, 'auto'
    return {
        'live': live is not None, 'source': source,
        'video_id': live['video_id'] if live else None,
        'title': s.popup_title or (live['title'] if live else ''),
        'channel_url': f'https://www.youtube.com/{s.channel_handle}',
        'whatsapp_number': s.whatsapp_number,
        'whatsapp_message': s.whatsapp_message,
    }
