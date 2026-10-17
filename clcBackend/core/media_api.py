"""Podcasts, discussions and posts shown on the website: managed by CLC Admins, read publicly."""
import os
import re

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.permissions import admin_api, log_staff_action

from .files import preview_ok, preview_query, range_response, read_image
from .models import MediaKind, MediaPost
from .packages_api import IMAGE_TYPES, MAX_IMAGE_BYTES, MAX_VIDEO_BYTES, VIDEO_TYPES, youtube_id

DURATION = re.compile(r'^\d{1,3}:[0-5]\d(:[0-5]\d)?$')


def media_json(m, admin=False):
    return {
        'id': str(m.id), 'kind': m.kind, 'kind_label': m.get_kind_display(), 'title': m.title, 'excerpt': m.excerpt,
        'body_text': m.body, 'body': [p.strip() for p in re.split(r'\n\s*\n', m.body) if p.strip()],
        'published_on': m.published_on, 'duration': m.duration,
        'cover_url': f'/api/media/{m.id}/cover/' + (preview_query(m.id) if admin else '') if m.cover else None,
        # player: 'youtube' (a link), 'video' (an uploaded file) or 'none' (the cover picture only)
        'player': 'youtube' if m.video_id else 'video' if m.video else 'none',
        'video_url': f'/api/media/{m.id}/video/' + (preview_query(m.id) if admin else '') if m.video else None, 'youtube_id': m.video_id,
        'is_published': m.is_published, 'updated_at': m.updated_at,
    }


def apply_input(m, data, files):
    """Validate and copy the form onto the item. Returns an error message or None."""
    if 'title' in data:
        title = (data.get('title') or '').strip()
        if not title or len(title) > 250:
            return 'Enter a title (up to 250 characters).'
        m.title = title
    if 'kind' in data:
        if data['kind'] not in MediaKind.values:
            return 'Choose Podcast, Mijadala or Machapisho.'
        m.kind = data['kind']
    if not m.kind:
        return 'Choose Podcast, Mijadala or Machapisho.'
    if 'excerpt' in data:
        m.excerpt = (data.get('excerpt') or '').strip()
    if 'body' in data:
        m.body = (data.get('body') or '').strip()
    if 'published_on' in data:
        day = parse_date(str(data.get('published_on') or ''))
        if day is None:
            return 'Enter the date it was published.'
        m.published_on = day
    elif not m.published_on:
        m.published_on = timezone.localdate()
    if 'duration' in data:
        duration = (data.get('duration') or '').strip()
        if duration and not DURATION.match(duration):
            return 'Write the duration like 24:10 (minutes:seconds).'
        m.duration = duration
    if 'is_published' in data:
        m.is_published = str(data['is_published']).lower() in ('true', '1', 'on')

    if str(data.get('remove_cover', '')).lower() in ('true', '1', 'on') and m.cover and not files.get('cover'):
        m.cover.delete(save=False)
        m.cover = None
    cover = files.get('cover')
    if cover:
        if os.path.splitext(cover.name)[1].lower() not in IMAGE_TYPES:
            return 'The cover must be a JPG, PNG or WEBP picture.'
        if cover.size > MAX_IMAGE_BYTES:
            return 'The cover picture can be at most 5 MB.'
        if m.cover:
            m.cover.delete(save=False)
        m.cover = cover

    player = data.get('player')
    if player in ('none', 'video', 'youtube'):
        if player != 'video' and m.video:
            m.video.delete(save=False)
            m.video = None
        if player != 'youtube':
            m.video_id = ''
    if player == 'youtube':
        vid = youtube_id(data.get('video_url'))
        if not vid:
            return 'Paste a valid YouTube link for the video.'
        m.video_id = vid
    elif player == 'video':
        upload = files.get('video')
        if upload:
            if os.path.splitext(upload.name)[1].lower() not in VIDEO_TYPES:
                return 'The video must be an MP4, WEBM or MOV file.'
            if upload.size > MAX_VIDEO_BYTES:
                return 'The video can be at most 200 MB.'
            if m.video:
                m.video.delete(save=False)
            m.video = upload
        elif not m.video:
            return 'Choose a video file to upload.'
    if not m.cover and not m.video_id and not m.video:
        return 'Add a cover picture, or a video: the card needs a picture to show.'
    return None


@admin_api(['GET', 'POST'])
def media_list(request):
    if request.method == 'GET':
        return Response([media_json(m, admin=True) for m in MediaPost.objects.all()])
    m = MediaPost()
    error = apply_input(m, request.data, request.FILES)
    if error:
        return Response({'detail': error}, status=400)
    m.save()
    log_staff_action(request, 'media_post', m.id, 'media.created', {'title': m.title, 'kind': m.kind})
    return Response(media_json(m, admin=True), status=201)


@admin_api(['PATCH', 'DELETE'])
def media_detail(request, media_id):
    m = get_object_or_404(MediaPost, id=media_id)
    if request.method == 'DELETE':
        log_staff_action(request, 'media_post', m.id, 'media.deleted', {'title': m.title})
        for f in (m.cover, m.video):
            if f:
                f.delete(save=False)
        m.delete()
        return Response(status=204)
    error = apply_input(m, request.data, request.FILES)
    if error:
        return Response({'detail': error}, status=400)
    m.save()
    log_staff_action(request, 'media_post', m.id, 'media.updated', {'title': m.title})
    return Response(media_json(m, admin=True))


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def public_media(request):
    return Response([media_json(m) for m in MediaPost.objects.filter(is_published=True)])


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def media_cover(request, media_id):
    m = get_object_or_404(MediaPost, id=media_id)
    if not m.cover or not (m.is_published or preview_ok(m.id, request.GET.get('sig'))):
        raise Http404
    return read_image(m.cover)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def media_video(request, media_id):
    m = get_object_or_404(MediaPost, id=media_id)
    if not m.video or not (m.is_published or preview_ok(m.id, request.GET.get('sig'))):
        raise Http404
    return range_response(request, m.video.path, 'video/mp4')
