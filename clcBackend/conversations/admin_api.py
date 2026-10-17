"""The CLC inbox: read what the bot and the client said, take over, and write to the client's WhatsApp from the web."""
import logging
from datetime import timedelta

from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from accounts.models import Membership
from accounts.permissions import admin_api, log_staff_action
from chatbot import whatsapp
from core.models import ActorType
from tracking.models import ActivityEvent

from .models import (BotSession, Conversation, ConversationStatus, DeliveryStatus, Message, MsgDirection, MsgType, SessionEnd)

log = logging.getLogger('conversations')
WINDOW = timedelta(hours=24)  # WhatsApp only allows free-form replies within 24 hours of the client's last message
MAX_TEXT = 4000

# What the admin sees first: clients waiting, then chats a person is handling, then the bot's
ORDER = {ConversationStatus.QUEUED: 0, ConversationStatus.WITH_STAFF: 1, ConversationStatus.BOT: 2, ConversationStatus.CLOSED: 3}


def is_test(client):
    """Conversations started from the flow builder's real test: nothing is ever sent to WhatsApp for them."""
    return (client.wa_id or '').startswith('test-')


def who(m):
    """Name and role, so everyone can tell an advocate from a CLC admin."""
    return f'{m.user.full_name} ({m.get_role_display()})'


def _window_open(c):
    return bool(c.last_inbound_at and timezone.now() - c.last_inbound_at < WINDOW)


def row(c, last_body=None, last_at=None):
    cl = c.client
    return {
        'id': str(c.id), 'client_name': cl.full_name or '', 'client_phone': cl.phone_e164 or '', 'is_test': is_test(cl),
        'status': c.status, 'status_label': c.get_status_display(),
        'last_message': (last_body or '')[:90], 'last_at': last_at or c.opened_at, 'opened_at': c.opened_at,
        'matter_id': str(c.case_id) if c.case_id else None, 'matter_reference': c.case.reference if c.case_id else None,
        'taken_over_by': who(c.taken_over_by) if c.taken_over_by_id else None,
        'window_open': _window_open(c) or is_test(cl),
    }


def message_json(m):
    return {'id': str(m.id), 'direction': m.direction, 'sender': m.sender_type, 'type': m.type, 'body': m.body or '',
            'staff': who(m.sent_as_membership) if m.sent_as_membership_id else None,
            'status': m.status, 'error': m.error_detail, 'at': m.created_at}


# What the chat shows as centred notes between the messages
EVENT_TEXT = {'conversation.taken_over': 'took over this chat', 'conversation.released_to_bot': 'gave this chat back to the bot',
              'conversation.closed': 'closed this chat'}


def events_json(c):
    evs = (ActivityEvent.objects.filter(entity_type='conversation', entity_id=c.id, action__in=EVENT_TEXT)
           .select_related('acting_membership__user').order_by('occurred_at'))
    return [{'id': f'e{e.id}', 'membership_id': str(e.acting_membership_id), 'who': who(e.acting_membership) if e.acting_membership_id else 'CLC team',
             'text': EVENT_TEXT[e.action], 'at': e.occurred_at} for e in evs]


def _me(request):
    return Membership.objects.select_related('user').get(id=request.auth.get('membership_id'))


@admin_api(['GET'])
def conversations(request):
    last = Message.objects.filter(conversation=OuterRef('pk')).order_by('-created_at')
    qs = (Conversation.objects.select_related('client', 'case', 'taken_over_by__user')
          .annotate(last_body=Subquery(last.values('body')[:1]), last_at=Subquery(last.values('created_at')[:1])))
    if request.GET.get('status'):
        qs = qs.filter(status=request.GET['status'])
    rows = sorted(qs, key=lambda c: (ORDER[c.status], -(c.last_at or c.opened_at).timestamp()))
    return Response([row(c, c.last_body, c.last_at) for c in rows])


@admin_api(['GET'])
def conversation_detail(request, conversation_id):
    c = get_object_or_404(Conversation.objects.select_related('client', 'case', 'taken_over_by__user'), id=conversation_id)
    return Response(_detail(c.id))


def take_over_chat(request, c, me):
    """The chat is now handled by a person: the bot stops replying."""
    c.status, c.taken_over_by = ConversationStatus.WITH_STAFF, me
    c.save(update_fields=['status', 'taken_over_by'])
    BotSession.objects.filter(conversation=c, ended_at__isnull=True).update(ended_at=timezone.now(), end_reason=SessionEnd.HANDOFF)
    log_staff_action(request, 'conversation', c.id, 'conversation.taken_over')


@admin_api(['POST'])
def take_over(request, conversation_id):
    c = get_object_or_404(Conversation, id=conversation_id)
    if c.status == ConversationStatus.CLOSED:
        return Response({'detail': 'This conversation is closed.'}, status=409)
    take_over_chat(request, c, _me(request))
    return Response(_detail(c.id))


def _detail(conversation_id):
    c = Conversation.objects.select_related('client', 'case', 'taken_over_by__user').get(id=conversation_id)
    msgs = c.messages.select_related('sent_as_membership__user').order_by('created_at')
    return {**row(c), 'messages': [message_json(m) for m in msgs], 'events': events_json(c)}


@admin_api(['POST'])
def release(request, conversation_id):
    """Give the chat back to the bot: it answers the client's next message."""
    c = get_object_or_404(Conversation, id=conversation_id)
    if c.status == ConversationStatus.CLOSED:
        return Response({'detail': 'This conversation is closed.'}, status=409)
    c.status, c.taken_over_by = ConversationStatus.BOT, None
    c.save(update_fields=['status', 'taken_over_by'])
    log_staff_action(request, 'conversation', c.id, 'conversation.released_to_bot')
    return Response(_detail(c.id))


@admin_api(['POST'])
def close(request, conversation_id):
    c = get_object_or_404(Conversation, id=conversation_id)
    if c.status != ConversationStatus.CLOSED:
        c.status, c.closed_at = ConversationStatus.CLOSED, timezone.now()
        c.save(update_fields=['status', 'closed_at'])
        BotSession.objects.filter(conversation=c, ended_at__isnull=True).update(ended_at=timezone.now(), end_reason=SessionEnd.HANDOFF)
        log_staff_action(request, 'conversation', c.id, 'conversation.closed')
    return Response(_detail(c.id))


@admin_api(['POST'])
def send(request, conversation_id):
    c = get_object_or_404(Conversation.objects.select_related('client'), id=conversation_id)
    return send_as_staff(request, c, _me(request))


def send_as_staff(request, c, me):
    """Write to the client on WhatsApp as a member of the team. Sending takes the chat over, so the bot stays quiet."""
    text = (request.data.get('text') or '').strip()
    if not text:
        return Response({'detail': 'Write a message first.'}, status=400)
    if len(text) > MAX_TEXT:
        return Response({'detail': f'A message can be at most {MAX_TEXT} characters.'}, status=400)
    if c.status == ConversationStatus.CLOSED:
        return Response({'detail': 'This conversation is closed.'}, status=409)
    test = is_test(c.client)
    to = (c.client.wa_id or (c.client.phone_e164 or '').lstrip('+')) if not test else None
    if not test:
        if not to:
            return Response({'detail': 'This client has no WhatsApp number.'}, status=409)
        if not _window_open(c):
            return Response({'code': 'window_closed',
                             'detail': 'WhatsApp only lets us write freely within 24 hours of the client\'s last message. '
                                       'It has been longer: wait for the client to write again.'}, status=409)

    if c.status != ConversationStatus.WITH_STAFF:
        take_over_chat(request, c, me)

    payload = {'type': 'text', 'text': {'body': text, 'preview_url': False}}
    msg = Message.objects.create(conversation=c, direction=MsgDirection.OUTBOUND, sender_type=ActorType.STAFF, sent_as_membership=me,
                                 type=MsgType.TEXT, body=text, payload={'meta': payload}, status=DeliveryStatus.QUEUED)
    if test:  # a builder test chat: there is nobody on WhatsApp to send to
        msg.status = DeliveryStatus.SENT
        msg.save(update_fields=['status'])
    else:
        try:
            wamid = whatsapp.send(to, payload)
            msg.provider_message_id, msg.status = wamid, DeliveryStatus.SENT
            msg.save(update_fields=['provider_message_id', 'status'])
        except Exception as exc:  # no keys yet, network or Meta error: keep a record and tell the admin
            log.exception('WhatsApp send failed')
            msg.status, msg.error_detail = DeliveryStatus.FAILED, str(exc)[:500]
            msg.save(update_fields=['status', 'error_detail'])
            return Response({'detail': f'WhatsApp did not accept the message: {msg.error_detail}', 'message': message_json(msg)}, status=502)
    log_staff_action(request, 'conversation', c.id, 'conversation.message_sent', {'characters': len(text)})
    return Response({'message': message_json(msg), 'conversation': _detail(c.id)}, status=201)
