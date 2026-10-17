import re
import uuid

from django.core.cache import cache
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.permissions import admin_api, log_staff_action

from . import live
from .models import LiveSettings
from .packages_api import youtube_id

LIVE_ENTITY = uuid.uuid5(uuid.NAMESPACE_URL, 'clc/live-settings')  # the audit trail needs an id for the single settings row


def settings_json(s):
    status = live.current_live()
    return {
        'channel_handle': s.channel_handle, 'popup_title': s.popup_title, 'whatsapp_number': s.whatsapp_number,
        'whatsapp_message': s.whatsapp_message, 'manual_live': s.manual_live,
        'manual_video_url': f'https://youtu.be/{s.manual_video_id}' if s.manual_video_id else '',
        'auto_detect': live.auto_detect_configured(),
        'status': {'live': status['live'], 'source': status['source'], 'video_id': status['video_id']},
    }


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def public_live(request):
    return Response(live.current_live())


@admin_api(['GET', 'PATCH'])
def live_settings(request):
    s = LiveSettings.load()
    if request.method == 'GET':
        return Response(settings_json(s))

    d = request.data
    if 'channel_handle' in d:
        handle = (d['channel_handle'] or '').strip()
        if not re.fullmatch(r'@?[\w.\-]{3,100}', handle):
            return Response({'detail': 'Enter the YouTube channel handle, like @Communitylegalclinic.'}, status=400)
        s.channel_handle = handle if handle.startswith('@') else f'@{handle}'
    if 'popup_title' in d:
        s.popup_title = (d['popup_title'] or '').strip()
    if 'whatsapp_number' in d:
        digits = re.sub(r'[\s\-+().]', '', d['whatsapp_number'] or '')
        if digits.startswith('0'):
            digits = '255' + digits[1:]
        if digits and not re.fullmatch(r'\d{9,15}', digits):
            return Response({'detail': 'Enter a valid WhatsApp number, e.g. +255 745 118 253.'}, status=400)
        s.whatsapp_number = digits
    if 'whatsapp_message' in d:
        s.whatsapp_message = (d['whatsapp_message'] or '').strip()[:300]
    if 'manual_video_url' in d:
        raw = (d['manual_video_url'] or '').strip()
        vid = youtube_id(raw) if raw else ''
        if raw and not vid:
            return Response({'detail': 'Paste a valid YouTube link for the live video.'}, status=400)
        s.manual_video_id = vid or ''
    if 'manual_live' in d:
        s.manual_live = str(d['manual_live']).lower() in ('true', '1', 'on')
    if s.manual_live and not s.manual_video_id:
        return Response({'detail': 'Paste the live video link before switching "Live now" on.'}, status=400)
    s.save()
    cache.delete(f'live:status:{s.channel_handle}')
    log_staff_action(request, 'live_settings', LIVE_ENTITY, 'live.updated', {'manual_live': s.manual_live})
    return Response(settings_json(s))
