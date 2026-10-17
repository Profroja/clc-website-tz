"""Packages and legal services shown on the website: managed by CLC Admins, read publicly."""
import json
import os
import re

from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.permissions import admin_api, log_staff_action

from .files import preview_ok, preview_query, range_response, read_image
from .models import ServicePackage

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
VIDEO_TYPES = {'.mp4', '.webm', '.mov', '.m4v'}
IMAGE_TYPES = {'.jpg', '.jpeg', '.png', '.webp'}
YOUTUBE = re.compile(r'(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/))([\w-]{11})')


def youtube_id(value):
    """Accepts a full YouTube link or a bare 11-character video ID."""
    value = (value or '').strip()
    if re.fullmatch(r'[\w-]{11}', value):
        return value
    m = YOUTUBE.search(value)
    return m.group(1) if m else None


def package_json(p, admin=False):
    return {
        'id': str(p.id), 'title': p.title, 'description': p.description, 'items': p.items,
        # media_type: 'youtube' (a link), 'video' (an uploaded file), 'image' or 'none'
        'media_type': 'youtube' if p.video_id else 'video' if p.video else 'image' if p.image else 'none',
        'image_url': f'/api/packages/{p.id}/image/' + (preview_query(p.id) if admin else '') if p.image else None,
        'video_url': f'/api/packages/{p.id}/video/' + (preview_query(p.id) if admin else '') if p.video else None, 'youtube_id': p.video_id,
        'is_published': p.is_published, 'sort_order': p.sort_order, 'updated_at': p.updated_at,
    }


def apply_input(p, data, files):
    """Validate and copy the form onto the package. Returns an error message or None."""
    if 'title' in data:
        title = (data.get('title') or '').strip()
        if not title or len(title) > 200:
            return 'Enter a title (up to 200 characters).'
        p.title = title
    if 'description' in data:
        p.description = (data.get('description') or '').strip()
    if 'items' in data:
        raw = data['items']
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return 'The list of services is not valid.'
        if not isinstance(items, list):
            return 'The list of services is not valid.'
        items = [str(i).strip() for i in items if str(i).strip()]
        if len(items) > 30 or any(len(i) > 200 for i in items):
            return 'Add at most 30 services, each up to 200 characters.'
        p.items = items
    if 'is_published' in data:
        p.is_published = str(data['is_published']).lower() in ('true', '1', 'on')

    media = data.get('media_type')
    if media in ('none', 'image', 'video', 'youtube'):
        # Only the chosen kind of media is kept; the others are removed.
        if media != 'image' and p.image:
            p.image.delete(save=False)
            p.image = None
        if media != 'video' and p.video:
            p.video.delete(save=False)
            p.video = None
        if media != 'youtube':
            p.video_id = ''
    if media == 'youtube':
        vid = youtube_id(data.get('video_url'))
        if not vid:
            return 'Paste a valid YouTube link for the video.'
        p.video_id = vid
    elif media == 'image':
        upload = files.get('image')
        if upload:
            if os.path.splitext(upload.name)[1].lower() not in IMAGE_TYPES:
                return 'The image must be a JPG, PNG or WEBP file.'
            if upload.size > MAX_IMAGE_BYTES:
                return 'The image can be at most 5 MB.'
            if p.image:
                p.image.delete(save=False)
            p.image = upload
        elif not p.image:
            return 'Choose an image to upload.'
    elif media == 'video':
        upload = files.get('video')
        if upload:
            if os.path.splitext(upload.name)[1].lower() not in VIDEO_TYPES:
                return 'The video must be an MP4, WEBM or MOV file.'
            if upload.size > MAX_VIDEO_BYTES:
                return 'The video can be at most 200 MB.'
            if p.video:
                p.video.delete(save=False)
            p.video = upload
        elif not p.video:
            return 'Choose a video file to upload.'
    return None


@admin_api(['GET', 'POST'])
def packages(request):
    if request.method == 'GET':
        return Response([package_json(p, admin=True) for p in ServicePackage.objects.all()])
    p = ServicePackage()
    error = apply_input(p, request.data, request.FILES)
    if error:
        return Response({'detail': error}, status=400)
    p.sort_order = (ServicePackage.objects.order_by('-sort_order').values_list('sort_order', flat=True).first() or 0) + 1
    p.save()
    log_staff_action(request, 'service_package', p.id, 'package.created', {'title': p.title})
    return Response(package_json(p, admin=True), status=201)


@admin_api(['PATCH', 'DELETE'])
def package_detail(request, package_id):
    p = get_object_or_404(ServicePackage, id=package_id)
    if request.method == 'DELETE':
        log_staff_action(request, 'service_package', p.id, 'package.deleted', {'title': p.title})
        for f in (p.image, p.video):
            if f:
                f.delete(save=False)
        p.delete()
        return Response(status=204)
    error = apply_input(p, request.data, request.FILES)
    if error:
        return Response({'detail': error}, status=400)
    p.save()
    log_staff_action(request, 'service_package', p.id, 'package.updated', {'title': p.title})
    return Response(package_json(p, admin=True))


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def public_packages(request):
    return Response([package_json(p) for p in ServicePackage.objects.filter(is_published=True)])


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def package_image(request, package_id):
    """Only the package image is public; the rest of the storage folder (client and firm documents) is not."""
    p = get_object_or_404(ServicePackage, id=package_id)
    if not p.image or not (p.is_published or preview_ok(p.id, request.GET.get('sig'))):
        raise Http404
    return read_image(p.image)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def package_video(request, package_id):
    p = get_object_or_404(ServicePackage, id=package_id)
    if not p.video or not (p.is_published or preview_ok(p.id, request.GET.get('sig'))):
        raise Http404
    return range_response(request, p.video.path, 'video/mp4')
