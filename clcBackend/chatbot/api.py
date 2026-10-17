"""REST API for the flow builder in the CLC Admin Portal (Chatbot flows screen)."""
import hashlib
import io
import json
import logging
import mimetypes
import re
import uuid

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.permissions import admin_api
from conversations.models import BotFlow, BotFlowVersion, BotSession, FlowStatus, FlowTrigger, TriggerType
from core.models import ActorType
from tracking.models import ActivityEvent, UserSession

from . import botsettings, realtest, service, waconfig, whatsapp
from .catalog import catalog
from .effects import SimulatedEffects
from .models import MediaAsset, MediaKind, WhatsAppConnection
from .runtime import FlowError, Runtime
from .store import TestStore
from .validate import has_errors, validate

log = logging.getLogger('chatbot')
MAX_DEFINITION_NODES = 300


def _log(request, flow_id, action, after=None):
    ActivityEvent.objects.create(
        actor_type=ActorType.STAFF, session_id=request.auth.get('session_id'),
        acting_membership_id=request.auth.get('membership_id'), entity_type='bot_flow', entity_id=flow_id,
        action=action, after_data=after,
    )


def blank_definition():
    return {'nodes': [{'id': 'start', 'type': 'start', 'position': {'x': 0, 'y': 0}, 'data': {}}],
            'edges': [], 'settings': {'languages': ['sw', 'en']}}


def _languages(definition):
    return tuple((definition.get('settings') or {}).get('languages') or ('sw', 'en'))


def _draft(flow, create=True):
    """The flow's editable draft; made from the published version when there is none."""
    draft = flow.versions.filter(status=FlowStatus.DRAFT).first()
    if draft or not create:
        return draft
    published = flow.versions.filter(status=FlowStatus.PUBLISHED).first()
    number = (flow.versions.aggregate(m=Max('version'))['m'] or 0) + 1
    return BotFlowVersion.objects.create(flow=flow, version=number, status=FlowStatus.DRAFT,
                                         definition=published.definition if published else blank_definition())


def _editing_definition(flow):
    """Draft if there is one, else the live version; never creates anything."""
    source = _draft(flow, create=False) or flow.versions.filter(status=FlowStatus.PUBLISHED).first()
    return source.definition if source else blank_definition()


def _lookup_media(asset_id):
    try:
        return MediaAsset.objects.filter(id=asset_id).exists()
    except (ValueError, ValidationError):
        return False


def _lookup_flow(flow_id):
    flow = BotFlow.objects.filter(id=flow_id).first()
    if flow is None:
        return None
    return {'name': flow.name, 'published': flow.versions.filter(status=FlowStatus.PUBLISHED).exists()}


def _trigger_json(t):
    return {'id': str(t.id), 'type': t.type, 'config': t.config, 'priority': t.priority, 'is_active': t.is_active}


def _flow_json(flow, with_definition=False):
    published = next((v for v in flow.versions.all() if v.status == FlowStatus.PUBLISHED), None)
    draft = next((v for v in flow.versions.all() if v.status == FlowStatus.DRAFT), None)
    data = {
        'id': str(flow.id), 'name': flow.name, 'description': flow.description, 'is_active': flow.is_active,
        'triggers': [_trigger_json(t) for t in flow.triggers.all()],
        'published': {'version': published.version, 'published_at': published.published_at,
                      'nodes': len(published.definition.get('nodes', []))} if published else None,
        'draft': {'version': draft.version, 'updated_at': draft.updated_at,
                  'nodes': len(draft.definition.get('nodes', []))} if draft else None,
        'updated_at': flow.updated_at,
    }
    if with_definition:
        # What the builder opens: the draft if there are unpublished changes, else the live version.
        # Opening a flow never creates a draft; the first save does.
        source = draft or published
        data['editing'] = {
            'from': 'draft' if draft else 'published' if published else 'blank',
            'version': source.version if source else 1,
            'definition': source.definition if source else blank_definition(),
        }
    return data


def _check_definition(definition):
    if not isinstance(definition, dict) or not isinstance(definition.get('nodes'), list) \
            or not isinstance(definition.get('edges'), list):
        return 'definition must be {"nodes": [...], "edges": [...]}'
    if len(definition['nodes']) > MAX_DEFINITION_NODES:
        return f'A flow can have at most {MAX_DEFINITION_NODES} blocks.'
    return None


def _clean_triggers(raw):
    clean = []
    for t in raw or []:
        if t.get('type') not in TriggerType.values:
            raise ValueError(f"Unknown trigger type {t.get('type')!r}")
        config = {}
        if t['type'] == TriggerType.KEYWORD:
            words = [str(w).strip() for w in (t.get('config') or {}).get('words', []) if str(w).strip()]
            if not words:
                raise ValueError('A keyword trigger needs at least one word.')
            config = {'words': words[:50],
                      'match': 'contains' if (t.get('config') or {}).get('match') == 'contains' else 'exact'}
        clean.append({'type': t['type'], 'config': config, 'priority': int(t.get('priority') or 100),
                      'is_active': bool(t.get('is_active', True))})
    return clean


# --- endpoints ---------------------------------------------------------------------

@admin_api(['GET'])
def node_types(request):
    return Response(catalog())


@admin_api(['GET', 'POST'])
def flows(request):
    if request.method == 'GET':
        qs = BotFlow.objects.prefetch_related('versions', 'triggers').order_by('name')
        return Response([_flow_json(f) for f in qs])

    name = (request.data.get('name') or '').strip()
    if not name:
        return Response({'detail': 'Give the flow a name.'}, status=400)
    if BotFlow.objects.filter(name__iexact=name).exists():
        return Response({'detail': 'A flow with this name already exists.'}, status=400)
    with transaction.atomic():
        flow = BotFlow.objects.create(name=name, description=request.data.get('description') or '')
        BotFlowVersion.objects.create(flow=flow, version=1, status=FlowStatus.DRAFT, definition=blank_definition())
        _log(request, flow.id, 'flow.created', {'name': name})
    return Response(_flow_json(BotFlow.objects.prefetch_related('versions', 'triggers').get(id=flow.id), True),
                    status=201)


@admin_api(['GET', 'PATCH', 'DELETE'])
def flow_detail(request, flow_id):
    flow = get_object_or_404(BotFlow, id=flow_id)
    if request.method == 'GET':
        flow = BotFlow.objects.prefetch_related('versions', 'triggers').get(id=flow.id)
        return Response(_flow_json(flow, with_definition=True))

    if request.method == 'DELETE':
        if BotSession.objects.filter(flow_version__flow=flow).exists():
            return Response({'detail': 'Clients have already used this flow, so its history must be kept. '
                                       'Switch it off instead of deleting it.'}, status=409)
        users = [f.name for f in BotFlow.objects.exclude(id=flow.id).prefetch_related('versions')
                 if any(n.get('type') == 'go_to_flow' and str((n.get('data') or {}).get('flow_id')) == str(flow.id)
                        for v in f.versions.all() for n in v.definition.get('nodes', []))]
        if users:
            return Response({'detail': f"Other flows jump to this one ({', '.join(users)}). "
                                       'Remove those blocks before deleting it.'}, status=409)
        with transaction.atomic():
            _log(request, flow.id, 'flow.deleted', {'name': flow.name})
            flow.triggers.all().delete()
            flow.versions.all().delete()
            flow.delete()
        return Response(status=204)

    changes = {}
    with transaction.atomic():
        for key in ('name', 'description'):
            if key in request.data:
                value = (request.data.get(key) or '').strip()
                if key == 'name' and (not value or BotFlow.objects.filter(name__iexact=value).exclude(id=flow.id).exists()):
                    return Response({'detail': 'Choose a different, non-empty name.'}, status=400)
                setattr(flow, key, value)
                changes[key] = value
        if 'is_active' in request.data:
            flow.is_active = bool(request.data['is_active'])
            changes['is_active'] = flow.is_active
        flow.save()
        if 'triggers' in request.data:
            try:
                clean = _clean_triggers(request.data['triggers'])
            except (ValueError, TypeError) as exc:
                transaction.set_rollback(True)
                return Response({'detail': str(exc)}, status=400)
            flow.triggers.all().delete()
            FlowTrigger.objects.bulk_create([FlowTrigger(flow=flow, **t) for t in clean])
            changes['triggers'] = clean
        _log(request, flow.id, 'flow.updated', changes)
    flow = BotFlow.objects.prefetch_related('versions', 'triggers').get(id=flow.id)
    return Response(_flow_json(flow))


@admin_api(['PUT'])
def save_draft(request, flow_id):
    flow = get_object_or_404(BotFlow, id=flow_id)
    definition = request.data.get('definition')
    problem = _check_definition(definition)
    if problem:
        return Response({'detail': problem}, status=400)
    with transaction.atomic():
        draft = _draft(flow)
        draft.definition = definition
        draft.save(update_fields=['definition', 'updated_at'])
    issues = validate(definition, flow_id=flow.id, lookup_flow=_lookup_flow, lookup_media=_lookup_media, languages=_languages(definition))
    return Response({'version': draft.version, 'updated_at': draft.updated_at, 'issues': issues})


@admin_api(['POST'])
def validate_flow(request, flow_id):
    flow = get_object_or_404(BotFlow, id=flow_id)
    definition = request.data.get('definition') or _editing_definition(flow)
    problem = _check_definition(definition)
    if problem:
        return Response({'detail': problem}, status=400)
    return Response({'issues': validate(definition, flow_id=flow.id, lookup_flow=_lookup_flow, lookup_media=_lookup_media,
                                        languages=_languages(definition))})


@admin_api(['POST'])
def publish(request, flow_id):
    flow = get_object_or_404(BotFlow, id=flow_id)
    with transaction.atomic():
        draft = _draft(flow, create=False)
        if draft is None:
            return Response({'detail': 'Nothing to publish: there are no unpublished changes.'}, status=400)
        issues = validate(draft.definition, flow_id=flow.id, lookup_flow=_lookup_flow, lookup_media=_lookup_media,
                          languages=_languages(draft.definition))
        if has_errors(issues):
            return Response({'detail': 'Fix the errors before publishing.', 'issues': issues}, status=400)
        flow.versions.filter(status=FlowStatus.PUBLISHED).update(status=FlowStatus.ARCHIVED)
        draft.status = FlowStatus.PUBLISHED
        draft.published_at = timezone.now()
        draft.published_by_id = request.auth.get('membership_id')
        draft.save()
        _log(request, flow.id, 'flow.published', {'version': draft.version})
    flow = BotFlow.objects.prefetch_related('versions', 'triggers').get(id=flow.id)
    return Response({**_flow_json(flow), 'issues': issues})


@admin_api(['GET'])
def versions(request, flow_id):
    flow = get_object_or_404(BotFlow, id=flow_id)
    rows = flow.versions.select_related('published_by__user').order_by('-version')
    return Response([{
        'id': str(v.id), 'version': v.version, 'status': v.status, 'published_at': v.published_at,
        'published_by': v.published_by.user.full_name if v.published_by else None,
        'updated_at': v.updated_at, 'nodes': len(v.definition.get('nodes', [])),
    } for v in rows])


@admin_api(['POST'])
def restore_version(request, flow_id, version_id):
    flow = get_object_or_404(BotFlow, id=flow_id)
    source = get_object_or_404(BotFlowVersion, id=version_id, flow=flow)
    with transaction.atomic():
        draft = _draft(flow)
        draft.definition = source.definition
        draft.save(update_fields=['definition', 'updated_at'])
        _log(request, flow.id, 'flow.version_restored', {'from_version': source.version})
    return Response({'version': draft.version, 'definition': draft.definition})


@admin_api(['POST'])
def simulate(request, flow_id):
    """The builder's test chat. Runs the canvas as it is (saved or not); nothing is saved
    and no WhatsApp message is sent. The client keeps `state` between calls."""
    flow = get_object_or_404(BotFlow, id=flow_id)
    definition = request.data.get('definition') or _editing_definition(flow)
    problem = _check_definition(definition)
    if problem:
        return Response({'detail': problem}, status=400)
    real = bool(request.data.get('real'))  # "real" test: matters are really created and the chat is kept for the inbox
    state, event = request.data.get('state'), request.data.get('event')
    if real:
        conv = realtest.conversation(fresh=not state)
        effects = realtest.RealTestEffects(conv.client, conv, hours=request.data.get('hours'))
    else:
        conv, effects = None, SimulatedEffects(hours=request.data.get('hours'))
    runtime = Runtime(TestStore(flow.id, definition), effects)
    try:
        if state:
            turn = runtime.receive(state, event or {'kind': 'other'})
        else:
            lang = request.data.get('lang') if request.data.get('lang') in ('sw', 'en', 'zh') else 'sw'
            turn = runtime.start(flow.id, {'lang': lang, 'client_name': 'Test Client', 'phone': '+255700000000'})
    except FlowError as exc:
        return Response({'error': str(exc)}, status=200)
    extra = {}
    if real:
        realtest.record(conv, event if state else None, request.data.get('label'), turn.messages)
        extra = {'real': True, 'matters': effects.matters, 'conversation_id': str(conv.id)}
    return Response({'messages': turn.messages, 'state': turn.state, 'ended': turn.ended, 'trace': turn.trace,
                     'waiting_node': turn.waiting_node, 'effects': effects.log, **extra})


# --- Bot files (Send file block) ----------------------------------------------------

# What WhatsApp accepts, and its size limits.
MEDIA_TYPES = {
    'image/jpeg': (MediaKind.IMAGE, 5), 'image/png': (MediaKind.IMAGE, 5),
    'application/pdf': (MediaKind.DOCUMENT, 100), 'application/msword': (MediaKind.DOCUMENT, 100),
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': (MediaKind.DOCUMENT, 100),
    'application/vnd.ms-excel': (MediaKind.DOCUMENT, 100),
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': (MediaKind.DOCUMENT, 100),
}
_signer = signing.TimestampSigner(salt='chatbot.media')


def _media_json(a):
    return {'id': str(a.id), 'name': a.name, 'kind': a.kind, 'file_name': a.file_name, 'mime_type': a.mime_type,
            'size_bytes': a.size_bytes, 'created_at': a.created_at,
            # The builder shows previews with <img>/<a>, which cannot send the login token.
            'url': f'/api/chatbot/media/{a.id}/file/?sig={_signer.sign(str(a.id)).split(":", 1)[1]}'}


def _flows_using(asset_id):
    """Names of flows whose draft or live version sends this file."""
    versions = BotFlowVersion.objects.select_related('flow').filter(status__in=[FlowStatus.DRAFT, FlowStatus.PUBLISHED])
    return sorted({v.flow.name for v in versions if str(asset_id) in json.dumps(v.definition)})


@admin_api(['GET', 'POST'])
def media(request):
    if request.method == 'GET':
        return Response([_media_json(a) for a in MediaAsset.objects.all()])
    got, error = _read_upload(request)
    if error:
        return error
    upload, mime, kind, data = got
    key = _store(upload, data)
    name = (request.data.get('name') or upload.name.rsplit('.', 1)[0])[:120]
    asset = MediaAsset.objects.create(
        name=name, kind=kind, file_name=upload.name[:200], mime_type=mime, size_bytes=len(data), storage_key=key,
        sha256=hashlib.sha256(data).hexdigest(), uploaded_by=request.user)
    _log(request, asset.id, 'bot_file.uploaded', {'name': name, 'file_name': asset.file_name})
    return Response(_media_json(asset), status=201)


def _read_upload(request):
    """(upload, mime, kind, data) or (None, error Response)."""
    upload = request.FILES.get('file')
    if not upload:
        return None, Response({'detail': 'Choose a file to upload.'}, status=400)
    mime = upload.content_type or mimetypes.guess_type(upload.name)[0] or ''
    if mime not in MEDIA_TYPES:
        return None, Response({'detail': 'WhatsApp can send JPG or PNG pictures, and PDF, Word or Excel documents.'},
                              status=400)
    kind, limit_mb = MEDIA_TYPES[mime]
    if upload.size > limit_mb * 1024 * 1024:
        return None, Response({'detail': f'WhatsApp allows {"pictures" if kind == MediaKind.IMAGE else "documents"} '
                                         f'up to {limit_mb} MB.'}, status=400)
    return (upload, mime, kind, upload.read()), None


def _store(upload, data):
    ext = upload.name.rsplit('.', 1)[-1].lower() if '.' in upload.name else 'bin'
    return default_storage.save(f'bot-files/{uuid.uuid4()}.{ext}', ContentFile(data))


@admin_api(['PATCH', 'POST', 'DELETE'])
def media_detail(request, asset_id):
    """PATCH renames; POST (multipart) replaces the file, so flows that send it get the new one."""
    asset = get_object_or_404(MediaAsset, id=asset_id)
    if request.method == 'POST':
        got, error = _read_upload(request)
        if error:
            return error
        upload, mime, kind, data = got
        old_key = asset.storage_key
        asset.storage_key, asset.file_name, asset.mime_type, asset.kind = _store(upload, data), upload.name[:200], mime, kind
        asset.size_bytes, asset.sha256 = len(data), hashlib.sha256(data).hexdigest()
        asset.meta_media_id, asset.meta_uploaded_at = '', None  # Meta must get the new file
        asset.save()
        default_storage.delete(old_key)
        _log(request, asset.id, 'bot_file.replaced', {'name': asset.name, 'file_name': asset.file_name})
        return Response(_media_json(asset))
    if request.method == 'PATCH':
        name = (request.data.get('name') or '').strip()
        if not name:
            return Response({'detail': 'Give the file a name.'}, status=400)
        asset.name = name[:120]
        asset.save(update_fields=['name'])
        return Response(_media_json(asset))
    used = _flows_using(asset.id)
    if used:
        return Response({'detail': f'Used in {", ".join(used)}. Remove it from those flows first.'}, status=409)
    _log(request, asset.id, 'bot_file.deleted', {'name': asset.name})
    asset.delete()
    return Response(status=204)


def media_file(request, asset_id):
    """The file itself, for previews in the builder (signed link, valid for a day)."""
    try:
        _signer.unsign(f'{asset_id}:{request.GET.get("sig", "")}', max_age=86400)
    except signing.BadSignature:
        raise Http404
    asset = get_object_or_404(MediaAsset, id=asset_id)
    with default_storage.open(asset.storage_key, 'rb') as f:  # read and release the file at once: Windows cannot replace or delete an open file
        data = f.read()
    return FileResponse(io.BytesIO(data), content_type=asset.mime_type,
                        filename=asset.file_name, as_attachment=asset.kind == MediaKind.DOCUMENT)


# --- Bot settings (business hours, staff emails) -------------------------------------

def _settings_json():
    hours = botsettings.get('business_hours')
    return {'business_hours': hours, 'hours_summary': botsettings.describe(hours),
            'open_now': botsettings.is_open(hours), 'staff_emails': botsettings.get('staff_emails'),
            'timezone': 'Africa/Dar_es_Salaam'}


@admin_api(['GET', 'PUT'])
def bot_settings(request):
    if request.method == 'PUT':
        if 'business_hours' in request.data:
            hours, problem = botsettings.clean_hours(request.data['business_hours'])
            if problem:
                return Response({'detail': problem}, status=400)
            botsettings.put('business_hours', hours)
        if 'staff_emails' in request.data:
            emails, problem = botsettings.clean_emails(request.data['staff_emails'])
            if problem:
                return Response({'detail': problem}, status=400)
            botsettings.put('staff_emails', emails)
        ActivityEvent.objects.create(
            actor_type=ActorType.STAFF, session_id=request.auth.get('session_id'),
            acting_membership_id=request.auth.get('membership_id'), entity_type='bot_settings',
            entity_id=uuid.UUID(int=0), action='bot_settings.updated', after_data=_settings_json() | {'open_now': None})
    return Response(_settings_json())


# --- WhatsApp connections ---------------------------------------------------------------

def _conn_json(c, in_use):
    def hint(v):
        return ('••••' + v[-4:]) if len(v) > 8 else ('••••' if v else '')
    return {'id': str(c.id), 'name': c.name, 'phone_number_id': c.phone_number_id, 'business_account_id': c.business_account_id,
            'api_version': c.api_version, 'token_hint': hint(c.access_token), 'has_token': bool(c.access_token),
            'has_secret': bool(c.app_secret), 'has_verify': bool(c.verify_token), 'in_use': in_use, 'created_at': c.created_at}


def _wa_list(request):
    active = waconfig.active()
    env_set = bool(waconfig.env('phone_number_id') and waconfig.env('access_token'))
    return {
        'webhook_url': request.build_absolute_uri('/api/whatsapp/webhook/'),
        'env': {'available': env_set, 'phone_number_id': waconfig.env('phone_number_id'), 'in_use': active is None and env_set},
        'connections': [_conn_json(c, c.is_active) for c in WhatsAppConnection.objects.all()],
    }


def _wa_audit(request, action, after):
    ActivityEvent.objects.create(
        actor_type=ActorType.STAFF, session_id=request.auth.get('session_id'),
        acting_membership_id=request.auth.get('membership_id'), entity_type='bot_settings',
        entity_id=uuid.UUID(int=1), action=action, after_data=after)


def _wa_apply(c, d, creating):
    """Copies the posted fields onto the connection; returns an error message or None."""
    for name in ('name',) + waconfig.PLAIN:
        if name in d:
            setattr(c, name, str(d[name] or '').strip())
    for name in waconfig.SECRETS:
        if str(d.get(name) or '').strip():  # blank = keep what is stored
            setattr(c, name, str(d[name]).strip())
    if not c.name:
        return 'Give the connection a name.'
    if not c.phone_number_id.isdigit():
        return 'The phone number ID is made of digits only.'
    if c.business_account_id and not c.business_account_id.isdigit():
        return 'The business account ID is made of digits only.'
    if c.api_version and not re.fullmatch(r'v\d{1,2}\.\d', c.api_version):
        return 'The API version looks like v23.0.'
    if creating and not c.access_token:
        return 'Enter the access token.'
    return None


@admin_api(['GET', 'POST'])
def whatsapp_connections(request):
    """The table of WhatsApp connections an admin added. Secrets are never sent back, only whether they are set."""
    if request.method == 'POST':
        c = WhatsAppConnection()
        problem = _wa_apply(c, request.data, creating=True)
        if problem:
            return Response({'detail': problem}, status=400)
        c.is_active = not WhatsAppConnection.objects.filter(is_active=True).exists() and bool(request.data.get('use_now'))
        c.save()
        _wa_audit(request, 'whatsapp_connection.added', {'name': c.name, 'phone_number_id': c.phone_number_id})
        return Response(_wa_list(request), status=201)
    return Response(_wa_list(request))


@admin_api(['PATCH', 'DELETE'])
def whatsapp_connection(request, connection_id):
    c = get_object_or_404(WhatsAppConnection, id=connection_id)
    if request.method == 'DELETE':
        if c.is_active:
            return Response({'detail': 'This connection is in use. Choose another one first.'}, status=409)
        c.delete()
        _wa_audit(request, 'whatsapp_connection.deleted', {'name': c.name})
        return Response(_wa_list(request))
    problem = _wa_apply(c, request.data, creating=False)
    if problem:
        return Response({'detail': problem}, status=400)
    c.save()
    _wa_audit(request, 'whatsapp_connection.updated', {'name': c.name, 'phone_number_id': c.phone_number_id})
    return Response(_wa_list(request))


@admin_api(['POST'])
def whatsapp_connection_use(request, connection_id=None):
    """Choose the connection the bot uses; without an id, go back to the values in the .env file."""
    with transaction.atomic():
        WhatsAppConnection.objects.filter(is_active=True).update(is_active=False)
        name = '.env file'
        if connection_id:
            c = get_object_or_404(WhatsAppConnection, id=connection_id)
            if not c.access_token:
                return Response({'detail': 'This connection has no access token.'}, status=400)
            c.is_active = True
            c.save(update_fields=['is_active'])
            name = c.name
    _wa_audit(request, 'whatsapp_connection.selected', {'name': name})
    return Response(_wa_list(request))


@admin_api(['POST'])
def whatsapp_connection_test(request, connection_id=None):
    """Ask Meta about the phone number with a saved connection (or the one in use), to show whether it works."""
    if connection_id:
        c = get_object_or_404(WhatsAppConnection, id=connection_id)
        number, token, version = c.phone_number_id, c.access_token, c.api_version or waconfig.env('api_version')
    else:
        number, token, version = waconfig.get('phone_number_id'), waconfig.get('access_token'), waconfig.get('api_version')
    if not (number and token):
        return Response({'ok': False, 'detail': 'Enter the phone number ID and the access token first.'})
    try:
        info = whatsapp.graph_get(f'{number}?fields=display_phone_number,verified_name,quality_rating', token, version)
    except Exception as exc:
        return Response({'ok': False, 'detail': str(exc)})
    return Response({'ok': True, 'display_phone_number': info.get('display_phone_number', ''), 'verified_name': info.get('verified_name', ''),
                     'quality_rating': info.get('quality_rating', '')})


# --- Meta webhook -------------------------------------------------------------------

@csrf_exempt
def webhook(request):
    if request.method == 'GET':  # Meta's one-time check when the webhook URL is registered
        tokens = waconfig.all_values('verify_token')
        if request.GET.get('hub.mode') == 'subscribe' and request.GET.get('hub.verify_token') in tokens:
            return HttpResponse(request.GET.get('hub.challenge', ''))
        return HttpResponse(status=403)
    if request.method != 'POST':
        return HttpResponse(status=405)
    if not whatsapp.valid_signature(request.body, request.headers.get('X-Hub-Signature-256')):
        return HttpResponse(status=401)
    try:
        read = service.handle_payload(json.loads(request.body))
        for message_id in read:
            try:
                whatsapp.mark_read(message_id)
            except Exception:
                log.warning('mark_read failed for %s', message_id)
    except Exception:
        # Answer 200 anyway: a non-200 makes Meta resend the same payload again and again.
        log.exception('WhatsApp webhook failed')
    return HttpResponse('OK')
