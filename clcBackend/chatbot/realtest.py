"""The builder's Test chat in "real" mode.

Normal test mode saves nothing. Real mode runs the same flow, but its blocks do what they do on WhatsApp:
a Create case block makes a real matter, a Handover really puts the chat in the CLC inbox, and the whole
conversation is kept so an admin can read it. It still never sends anything to WhatsApp and never emails
staff: the test client is a fixed record ("test-builder") that the inbox refuses to send to.
"""
from django.utils import timezone

from clients.models import Client
from conversations.models import (Conversation, ConversationStatus, DeliveryStatus, Message, MsgDirection, MsgType)
from core.models import ActorType, Channel

from . import whatsapp
from .live import LiveEffects

TEST_WA_ID = 'test-builder'


def test_client():
    return Client.objects.get_or_create(wa_id=TEST_WA_ID, defaults={'full_name': 'Test client (flow builder)'})[0]


def conversation(fresh):
    """The open test conversation. A fresh test closes the old one and starts a new one."""
    client = test_client()
    open_ones = Conversation.objects.filter(client=client, channel=Channel.WHATSAPP).exclude(status=ConversationStatus.CLOSED)
    if fresh:
        open_ones.update(status=ConversationStatus.CLOSED, closed_at=timezone.now())
        return Conversation.objects.create(client=client, last_inbound_at=timezone.now())
    return open_ones.order_by('-opened_at').first() or Conversation.objects.create(client=client, last_inbound_at=timezone.now())


class RealTestEffects(LiveEffects):
    """Live effects that also keep the list the test panel shows. Staff emails are only described."""

    def __init__(self, client, conversation, hours=None):
        super().__init__(client, conversation)
        self.log, self.hours, self.matters = [], hours, []

    def is_open(self):
        is_open = self.hours == 'open' if self.hours in ('open', 'closed') else super().is_open()
        self.log.append({'effect': 'business_hours', 'detail': 'open' if is_open else 'closed'})
        return is_open

    def create_case(self, **kw):
        kw['files'] = []  # the test panel's "documents" are placeholders: never store them as real files
        reference = super().create_case(**kw)
        self.matters.append(reference)
        self.log.append({'effect': 'create_case', 'detail': f'REAL matter {reference} created: see Admin > Matters'})
        return reference

    def handover(self):
        super().handover()
        self.log.append({'effect': 'handover', 'detail': 'REAL: the chat is now waiting in Admin > Conversations'})

    def set_client(self, field, value):
        super().set_client(field, value)
        self.log.append({'effect': 'set_client', 'detail': f'{field} = {value}'})

    def case_status(self):
        rows = super().case_status()
        self.log.append({'effect': 'case_status', 'detail': f'{len(rows)} real case(s) of the test client shown'})
        return rows

    def notify_staff(self, *, to, subject, body, answers):
        self.log.append({'effect': 'notify_staff', 'detail': f'NOT sent in a test: email "{subject}" would go to the staff list'})


def record(conv, event, label, messages):
    """Keep the test conversation so it can be read in the inbox."""
    if event:
        body = label or event.get('text') or event.get('choice_id') or event.get('kind')
        Message.objects.create(conversation=conv, direction=MsgDirection.INBOUND, sender_type=ActorType.CLIENT, type=MsgType.TEXT,
                               body=str(body)[:4000], payload={'event': {k: v for k, v in event.items() if k != 'file'}},
                               status=DeliveryStatus.RECEIVED)
        conv.last_inbound_at = timezone.now()
        conv.save(update_fields=['last_inbound_at'])
    for m in messages:
        payload = whatsapp.render(m)
        Message.objects.create(conversation=conv, direction=MsgDirection.OUTBOUND, sender_type=ActorType.BOT, type=payload['type'],
                               body=m.get('text') or m.get('caption') or m.get('name'), payload={'meta': payload},
                               status=DeliveryStatus.SENT)
