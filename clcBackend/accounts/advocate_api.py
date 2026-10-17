"""What an advocate sees: the dashboard, their assigned matters (each with its messages, documents,
appointments, notes and status), and their firm's subscription. An advocate only ever reaches matters
currently assigned to them."""
import io
from decimal import Decimal, InvalidOperation

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response

from appointments.models import Appointment, AppointmentStatus
from cases.admin_api import payments_json
from cases.models import CaseAssignment, CaseNote, CasePayment, CaseStatus, CaseStatusHistory, DocVisibility, PaymentMethod
from conversations import admin_api as inbox
from conversations.models import Conversation, ConversationStatus, Message

from .models import AdvocateProfile, Membership
from .permissions import advocate_api, log_staff_action

# What the advocate calls each stage
STATUS_LABEL = {CaseStatus.ADVOCATE_ASSIGNED: 'New', CaseStatus.ACTIVE: 'In progress', CaseStatus.CLOSED: 'Closed'}


def status_label(case):
    if case.ready_to_close_at and case.status != CaseStatus.CLOSED:
        return 'Ready to close'  # the advocate has finished; CLC closes it
    return STATUS_LABEL.get(case.status, case.get_status_display())


def _matter_row(a):
    c = a.case
    return {
        'id': str(c.id), 'reference': c.reference, 'service': c.service.name_en if c.service_id else None,
        'client': c.client.full_name or 'Client', 'region': c.region, 'priority': c.priority,
        'status': c.status, 'status_label': status_label(c), 'assigned_at': a.assigned_at, 'updated_at': c.updated_at,
    }


def _assignments(membership):
    return (CaseAssignment.objects.filter(advocate=membership, ended_at__isnull=True)
            .select_related('case__service', 'case__client').order_by('-case__updated_at'))


def _me(request):
    return Membership.objects.select_related('user', 'organization').get(id=request.auth.get('membership_id'))


def _assigned_case(request, case_id):
    """The matter, if it is assigned to the advocate working right now; otherwise nothing."""
    m = _me(request)
    a = (CaseAssignment.objects.filter(advocate=m, ended_at__isnull=True, case_id=case_id)
         .select_related('case__service', 'case__client', 'case__conversation').first())
    if a is None:
        raise Http404
    return m, a.case


def _subscription(org):
    app = getattr(org, 'application', None)
    if app is None:
        return None
    today = timezone.localdate()
    left = (app.trial_end - today).days if app.trial_end else None
    return {'status': app.status, 'status_label': app.get_status_display(), 'trial_start': app.trial_start,
            'trial_end': app.trial_end, 'billing_effective': app.billing_effective,
            'trial_days_left': max(left, 0) if left is not None else None,
            'in_trial': bool(app.trial_end and today <= app.trial_end and app.status == 'approved_for_trial')}


def _appointment_row(a):
    from appointments.availability import TZ
    local = a.scheduled_at.astimezone(TZ)
    return {'id': str(a.id), 'reference': a.reference, 'full_name': a.full_name, 'phone': a.phone_e164, 'email': a.email,
            'service': a.service, 'meeting_label': a.get_meeting_kind_display(), 'date': local.date().isoformat(),
            'time': local.strftime('%H:%M'), 'note': a.note, 'status': a.status}


def _my_appointments(m):
    return Appointment.objects.filter(advocate=m).order_by('scheduled_at')


# ---------------------------------------------------------------- dashboard

def _expertise(profile, app_adv):
    """The advocate's areas of expertise: from the profile, or from the firm's application for those approved before profiles held them."""
    for src in (profile, app_adv):
        if src is not None:
            names = [a.name_en for a in src.practice_areas.all()] + ([src.other_areas] if src.other_areas else [])
            if names:
                return names
    return []


@advocate_api(['GET'])
def overview(request):
    m = _me(request)
    user, org = m.user, m.organization
    profile = AdvocateProfile.objects.filter(user=user).first()
    app_adv = getattr(user, 'application_advocate', None)
    rows = [a for a in _assignments(m) if a.case.status != CaseStatus.CLOSED]
    today = timezone.localdate()
    expiry = profile.certificate_expires_on if profile else None
    return Response({
        'name': user.full_name,
        'stats': {
            'assigned': sum(1 for a in rows if a.case.status == CaseStatus.ADVOCATE_ASSIGNED),  # not started yet
            'in_progress': sum(1 for a in rows if a.case.status == CaseStatus.ACTIVE),
            'closed': sum(1 for a in _assignments(m) if a.case.status == CaseStatus.CLOSED),
        },
        'matters': [_matter_row(a) for a in rows],
        'details': {
            'full_name': user.full_name, 'email': user.email, 'phone': user.phone_e164 or '', 'law_firm': org.name,
            'roll_number': profile.roll_number if profile else '',
            'practising_certificate': profile.practising_certificate if profile else '',
            'certificate_expires_on': expiry, 'certificate_days_left': (expiry - today).days if expiry else None,
            'verified': bool(profile and profile.verified_at),
            'areas_of_expertise': _expertise(profile, app_adv),
        },
    })


@advocate_api(['GET'])
def subscription(request):
    m = _me(request)
    return Response({'law_firm': m.organization.name, 'subscription': _subscription(m.organization)})


@advocate_api(['GET'])
def appointments(request):
    return Response([_appointment_row(a) for a in _my_appointments(_me(request))])


# ---------------------------------------------------------------- matters

@advocate_api(['GET'])
def matters(request):
    out = []
    for a in _assignments(_me(request)):
        row = _matter_row(a)
        last = Message.objects.filter(conversation_id=a.case.conversation_id).order_by('-created_at').first() if a.case.conversation_id else None
        row['chat'] = {'has': bool(a.case.conversation_id), 'last_message': (last.body or '')[:90] if last else '', 'last_at': last.created_at if last else None}
        out.append(row)
    return Response(out)


@advocate_api(['GET'])
def matter_detail(request, case_id):
    m, c = _assigned_case(request, case_id)
    a = CaseAssignment.objects.get(case=c, ended_at__isnull=True)
    history = [{'from': h.from_status, 'to': h.to_status, 'at': h.changed_at, 'note': h.note,
                'by': h.changed_by.user.full_name if h.changed_by_id else 'System'}
               for h in c.status_history.select_related('changed_by__user').order_by('changed_at')]
    return Response({
        **_matter_row(a),
        'summary': c.summary or '', 'ready_to_close_at': c.ready_to_close_at, 'ready_to_close_summary': c.ready_to_close_summary,
        'history': history,
    })


def _chat_json(c, conv, me):
    """The conversation with the client, as the advocate sees it: no phone number, and who is handling it."""
    msgs = conv.messages.select_related('sent_as_membership__user').order_by('created_at')
    mine = conv.taken_over_by_id == me.id
    return {
        'conversation_id': str(conv.id), 'status': conv.status, 'mine': mine,
        'handled_by': None if mine or not conv.taken_over_by_id else inbox.who(conv.taken_over_by),
        'can_chat': c.status == CaseStatus.ACTIVE and conv.status != ConversationStatus.CLOSED,
        'window_open': inbox._window_open(conv) or inbox.is_test(conv.client),
        'messages': [{'id': str(x.id), 'direction': x.direction, 'sender': x.sender_type, 'type': x.type, 'body': x.body or '',
                      'mine': x.sent_as_membership_id == me.id, 'staff': inbox.who(x.sent_as_membership) if x.sent_as_membership_id else None, 'status': x.status, 'error': x.error_detail, 'at': x.created_at} for x in msgs],
        'events': [{**e, 'who': 'You' if e['membership_id'] == str(me.id) else e['who']} for e in inbox.events_json(conv)],
    }


def _chat(request, case_id):
    m, c = _assigned_case(request, case_id)
    conv = Conversation.objects.select_related('client', 'taken_over_by__user').filter(id=c.conversation_id).first() if c.conversation_id else None
    return m, c, conv


@advocate_api(['GET'])
def matter_messages(request, case_id):
    m, c, conv = _chat(request, case_id)
    if conv is None:
        return Response({'conversation_id': None, 'messages': [], 'events': [], 'can_chat': False})
    return Response(_chat_json(c, conv, m))


def _writable_chat(request, case_id):
    m, c, conv = _chat(request, case_id)
    if conv is None:
        return m, c, conv, Response({'detail': 'There is no conversation with this client.'}, status=409)
    if c.status != CaseStatus.ACTIVE:
        return m, c, conv, Response({'detail': 'Start work on this matter before chatting with the client.'}, status=409)
    if conv.status == ConversationStatus.CLOSED:
        return m, c, conv, Response({'detail': 'This conversation is closed.'}, status=409)
    return m, c, conv, None


@advocate_api(['POST'])
def matter_chat_take_over(request, case_id):
    m, c, conv, refused = _writable_chat(request, case_id)
    if refused:
        return refused
    if conv.taken_over_by_id != m.id or conv.status != ConversationStatus.WITH_STAFF:
        inbox.take_over_chat(request, conv, m)
        conv.refresh_from_db()
    return Response(_chat_json(c, conv, m))


@advocate_api(['POST'])
def matter_chat_release(request, case_id):
    m, c, conv, refused = _writable_chat(request, case_id)
    if refused:
        return refused
    conv.status, conv.taken_over_by = ConversationStatus.BOT, None
    conv.save(update_fields=['status', 'taken_over_by'])
    log_staff_action(request, 'conversation', conv.id, 'conversation.released_to_bot')
    return Response(_chat_json(c, conv, m))


@advocate_api(['POST'])
def matter_chat_send(request, case_id):
    """Write to the client on WhatsApp from the platform. Sending takes the chat over."""
    m, c, conv, refused = _writable_chat(request, case_id)
    if refused:
        return refused
    res = inbox.send_as_staff(request, conv, m)
    if res.status_code in (201, 502):
        conv = Conversation.objects.select_related('client', 'taken_over_by__user').get(id=conv.id)
        res = Response({'detail': res.data.get('detail'), 'chat': _chat_json(c, conv, m)}, status=res.status_code)
    return res


@advocate_api(['GET'])
def matter_documents(request, case_id):
    _, c = _assigned_case(request, case_id)
    docs = c.documents.filter(deleted_at__isnull=True, visibility=DocVisibility.SHARED_WITH_FIRM).order_by('-created_at')
    return Response([{'id': str(d.id), 'file_name': d.file_name, 'category': d.get_category_display(), 'mime_type': d.mime_type,
                      'size_bytes': d.size_bytes, 'at': d.created_at} for d in docs])


@advocate_api(['GET'])
def matter_document_download(request, case_id, doc_id):
    _, c = _assigned_case(request, case_id)
    doc = c.documents.filter(id=doc_id, deleted_at__isnull=True, visibility=DocVisibility.SHARED_WITH_FIRM).first()
    if doc is None:
        raise Http404
    with default_storage.open(doc.storage_key, 'rb') as f:  # read and release at once (Windows cannot move an open file)
        data = f.read()
    log_staff_action(request, 'document', doc.id, 'document.downloaded', {'case': c.reference})
    return FileResponse(io.BytesIO(data), as_attachment=True, filename=doc.file_name, content_type=doc.mime_type)


@advocate_api(['GET'])
def matter_appointments(request, case_id):
    m, _ = _assigned_case(request, case_id)
    return Response([_appointment_row(a) for a in _my_appointments(m)])


@advocate_api(['GET', 'POST'])
def matter_notes(request, case_id):
    """Notes belong to the advocate's own law firm: other firms and CLC never see them."""
    m, c = _assigned_case(request, case_id)
    if request.method == 'POST':
        body = (request.data.get('body') or '').strip()
        if not body:
            return Response({'detail': 'Write the note first.'}, status=400)
        if len(body) > 5000:
            return Response({'detail': 'A note can be at most 5000 characters.'}, status=400)
        CaseNote.objects.create(case=c, organization=m.organization, author=m, body=body)
        log_staff_action(request, 'case', c.id, 'case.note_added', {'reference': c.reference})
    notes = c.notes.filter(organization=m.organization).select_related('author__user').order_by('-created_at')
    return Response([{'id': str(n.id), 'body': n.body, 'author': n.author.user.full_name, 'at': n.created_at} for n in notes])


CURRENCIES = ('TZS', 'USD')


@advocate_api(['GET', 'POST'])
def matter_payments(request, case_id):
    """The assigned advocate records the payments involved in the matter."""
    m, c = _assigned_case(request, case_id)
    if request.method == 'POST':
        d = request.data
        purpose = (d.get('purpose') or '').strip()
        try:
            amount = Decimal(str(d.get('amount')).replace(',', '').strip())
        except (InvalidOperation, ValueError):
            amount = None
        paid_on = parse_date(str(d.get('paid_on') or ''))
        method = d.get('method') or PaymentMethod.CASH
        currency = (d.get('currency') or 'TZS').upper()
        if not purpose:
            return Response({'detail': 'Say what the payment was for.'}, status=400)
        if amount is None or not amount.is_finite() or amount <= 0 or amount >= Decimal('1e12'):
            return Response({'detail': 'Enter an amount greater than zero.'}, status=400)
        if amount.as_tuple().exponent < -2:
            return Response({'detail': 'An amount can have at most two decimals.'}, status=400)
        if paid_on is None or paid_on > timezone.localdate():
            return Response({'detail': 'Enter the date the payment was made (not in the future).'}, status=400)
        if method not in PaymentMethod.values or currency not in CURRENCIES:
            return Response({'detail': 'Choose a valid payment method and currency.'}, status=400)
        if c.status == CaseStatus.CLOSED:
            return Response({'detail': 'This matter is closed.'}, status=409)
        CasePayment.objects.create(case=c, recorded_by=m, purpose=purpose[:300], amount=amount, currency=currency, paid_on=paid_on,
                                   method=method, reference=(d.get('reference') or '').strip()[:100], note=(d.get('note') or '').strip()[:2000])
        log_staff_action(request, 'case', c.id, 'case.payment_recorded', {'reference': c.reference, 'amount': str(amount), 'currency': currency})
    return Response(payments_json(c))


def _record(case, m, from_status, to_status, note):
    CaseStatusHistory.objects.create(case=case, from_status=from_status, to_status=to_status, changed_by=m, note=note)


@advocate_api(['POST'])
def matter_start(request, case_id):
    """New -> In progress: the advocate has taken the matter on."""
    m, c = _assigned_case(request, case_id)
    if c.status != CaseStatus.ADVOCATE_ASSIGNED:
        return Response({'detail': 'This matter has already been started.'}, status=409)
    old, c.status = c.status, CaseStatus.ACTIVE
    c.save()
    _record(c, m, old, c.status, 'Advocate started work')
    log_staff_action(request, 'case', c.id, 'case.started', {'reference': c.reference})
    return Response({'status': c.status, 'status_label': status_label(c)})


@advocate_api(['POST'])
def matter_ready_to_close(request, case_id):
    """The advocate says the work is finished; CLC closes the matter."""
    m, c = _assigned_case(request, case_id)
    if c.status != CaseStatus.ACTIVE:
        return Response({'detail': 'Start the matter before marking it ready to close.'}, status=409)
    summary = (request.data.get('summary') or '').strip()
    if not summary:
        return Response({'detail': 'Write a short summary of the outcome.'}, status=400)
    c.ready_to_close_at, c.ready_to_close_summary = timezone.now(), summary[:3000]
    c.save()
    _record(c, m, c.status, c.status, f'Marked ready to close: {summary[:300]}')
    log_staff_action(request, 'case', c.id, 'case.ready_to_close', {'reference': c.reference})
    return Response({'ready_to_close_at': c.ready_to_close_at})
