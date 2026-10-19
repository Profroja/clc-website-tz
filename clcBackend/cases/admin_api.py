"""Matters for CLC Admins: see every matter, and assign it to an advocate of an approved law firm."""
import io
import logging
import uuid

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from accounts.models import AdvocateProfile, MemberRole, Membership, MembershipStatus, OrgStatus
from accounts.permissions import admin_api, log_staff_action
from notifications.models import Notification, NotifyChannel

from .models import Case, CaseAssignment, CaseNote, CaseOutcome, CasePayment, CaseStatus, CaseStatusHistory, Consent, Document, Referral, ReferralDocument

log = logging.getLogger('cases')

# Bot variables that are internal bookkeeping, not something the client told us
HIDDEN_ANSWERS = {'lang', 'lang_label', 'client_name', 'phone', 'files', 'reference', 'service_label', 'full_name'}

# Statuses from which an assignment moves the matter to "advocate assigned"
BEFORE_ASSIGNMENT = {CaseStatus.NEW, CaseStatus.UNDER_REVIEW, CaseStatus.REFERRED, CaseStatus.ACCEPTED, CaseStatus.REFERRED_BACK}


def _current(case):
    return next((a for a in case.assignments.all() if a.ended_at is None), None)


def row(c):
    a = _current(c)
    return {
        'id': str(c.id), 'reference': c.reference,
        'client_name': c.client.full_name or '', 'client_phone': c.client.phone_e164 or '',
        'is_test': (c.client.wa_id or '').startswith('test-'),
        'service': c.service.name_en if c.service_id else None, 'region': c.region or '', 'priority': c.priority,
        'status': c.status, 'ready_to_close': bool(c.ready_to_close_at and c.status != CaseStatus.CLOSED),
        'status_label': 'Ready to close' if c.ready_to_close_at and c.status != CaseStatus.CLOSED else c.get_status_display(),
        'advocate': a.advocate.user.full_name if a else None, 'firm': c.current_firm.name if c.current_firm_id else None,
        'created_at': c.created_at, 'updated_at': c.updated_at,
        'conversation_id': str(c.conversation_id) if c.conversation_id else None,
    }


def _queryset():
    return (Case.objects.select_related('client', 'service', 'current_firm')
            .prefetch_related('assignments__advocate__user').order_by('-created_at'))


@admin_api(['GET'])
def matters(request):
    qs = _queryset()
    if request.GET.get('status'):
        qs = qs.filter(status=request.GET['status'])
    if request.GET.get('unassigned') in ('1', 'true'):
        qs = qs.filter(~Exists(CaseAssignment.objects.filter(case=OuterRef('pk'), ended_at__isnull=True)))
    return Response([row(c) for c in qs])


def payments_json(case):
    """Payments recorded on a matter, newest first, with totals per currency."""
    rows = case.payments.select_related('recorded_by__user').order_by('-paid_on', '-created_at')
    totals = {}
    for p in rows:
        totals[p.currency] = totals.get(p.currency, 0) + p.amount
    return {
        'rows': [{'id': str(p.id), 'purpose': p.purpose, 'amount': str(p.amount), 'currency': p.currency, 'paid_on': p.paid_on,
                  'method': p.get_method_display(), 'reference': p.reference, 'note': p.note, 'recorded_by': p.recorded_by.user.full_name,
                  'at': p.created_at} for p in rows],
        'totals': [{'currency': c, 'amount': str(a)} for c, a in totals.items()],
    }


@admin_api(['GET'])
def matter_detail(request, case_id):
    c = get_object_or_404(_queryset(), id=case_id)
    return Response({
        **row(c),
        'summary': c.summary or '',
        'answers': [{'label': str(k).replace('_', ' ').capitalize(), 'value': v if isinstance(v, (str, int, float)) else str(v)}
                    for k, v in (c.answers or {}).items() if v not in (None, '') and k not in HIDDEN_ANSWERS],
        'ready_to_close_at': c.ready_to_close_at, 'ready_to_close_summary': c.ready_to_close_summary,
        'history': [{'from': h.from_status, 'to': h.to_status, 'at': h.changed_at, 'note': h.note,
                     'by': h.changed_by.user.full_name if h.changed_by_id else 'System'}
                    for h in c.status_history.select_related('changed_by__user').order_by('changed_at')],
        'documents': [{'id': str(d.id), 'file_name': d.file_name, 'category': d.get_category_display(), 'size_bytes': d.size_bytes,
                       'at': d.created_at} for d in c.documents.filter(deleted_at__isnull=True).order_by('-created_at')],
        'payments': payments_json(c),
    })


@admin_api(['GET'])
def matter_document_download(request, case_id, doc_id):
    c = get_object_or_404(Case, id=case_id)
    doc = c.documents.filter(id=doc_id, deleted_at__isnull=True).first()
    if doc is None:
        raise Http404
    with default_storage.open(doc.storage_key, 'rb') as f:  # read and release at once (Windows cannot move an open file)
        data = f.read()
    log_staff_action(request, 'document', doc.id, 'document.downloaded', {'case': c.reference})
    return FileResponse(io.BytesIO(data), as_attachment=True, filename=doc.file_name, content_type=doc.mime_type)


def assignable_advocates():
    """Advocates who can take a matter: active, verified, certificate valid, in an approved law firm."""
    today = timezone.localdate()
    out = []
    qs = (Membership.objects.filter(role=MemberRole.ADVOCATE, status=MembershipStatus.ACTIVE, user__is_active=True,
                                    organization__status=OrgStatus.APPROVED)
          .select_related('user', 'organization').order_by('organization__name', 'user__full_name'))
    for m in qs:
        p = AdvocateProfile.objects.filter(user=m.user).first()
        if p is None or p.verified_at is None or p.platform_suspended_at is not None:
            continue
        if p.certificate_expires_on is None or p.certificate_expires_on < today:
            continue
        open_matters = CaseAssignment.objects.filter(advocate=m, ended_at__isnull=True).exclude(case__status=CaseStatus.CLOSED).count()
        out.append({'id': str(m.id), 'name': m.user.full_name, 'firm': m.organization.name, 'open_matters': open_matters})
    return out


@admin_api(['GET'])
def advocates(request):
    return Response(assignable_advocates())


@admin_api(['POST'])
def assign(request, case_id):
    case = get_object_or_404(_queryset(), id=case_id)
    if case.status == CaseStatus.CLOSED:
        return Response({'detail': 'This matter is closed.'}, status=409)
    try:
        wanted = uuid.UUID(str(request.data.get('advocate_id')))
    except ValueError:
        wanted = None
    advocate = Membership.objects.select_related('user', 'organization').filter(id=wanted).first() if wanted else None
    if advocate is None or str(advocate.id) not in {a['id'] for a in assignable_advocates()}:
        return Response({'detail': 'Choose an active, verified advocate of an approved law firm.'}, status=400)
    admin = Membership.objects.get(id=request.auth.get('membership_id'))
    current = _current(case)
    if current and current.advocate_id == advocate.id:
        return Response({'detail': 'This matter is already assigned to that advocate.'}, status=409)

    with transaction.atomic():
        if current:
            current.ended_at, current.end_reason = timezone.now(), 'Reassigned by CLC'
            current.save(update_fields=['ended_at', 'end_reason'])
        old_status = case.status
        case.current_firm = advocate.organization  # must be set first: the assignment is checked against the firm holding the matter
        if case.status in BEFORE_ASSIGNMENT:
            case.status = CaseStatus.ADVOCATE_ASSIGNED
        case.save()
        CaseAssignment.objects.create(case=case, advocate=advocate, assigned_by=admin)
        CaseStatusHistory.objects.create(case=case, from_status=old_status, to_status=case.status, changed_by=admin,
                                         note=f'Assigned to {advocate.user.full_name} ({advocate.organization.name}) by CLC')
        log_staff_action(request, 'case', case.id, 'case.assigned', {'reference': case.reference, 'advocate': advocate.user.full_name})
        Notification.objects.create(membership=advocate, channel=NotifyChannel.IN_APP, subject='A matter was assigned to you',
                                    body=case.reference, entity_type='case', entity_id=case.id)
    _email_advocate(advocate, case)
    case = get_object_or_404(_queryset(), id=case_id)
    return Response(row(case))


@admin_api(['POST'])
def close_matter(request, case_id):
    """CLC closes a matter the advocate marked ready to close. A comment is optional."""
    case = get_object_or_404(_queryset(), id=case_id)
    if case.status == CaseStatus.CLOSED:
        return Response({'detail': 'This matter is already closed.'}, status=409)
    if case.ready_to_close_at is None:
        return Response({'detail': 'The advocate has not marked this matter ready to close yet.'}, status=409)
    comment = (request.data.get('comment') or '').strip()[:3000]
    admin = Membership.objects.get(id=request.auth.get('membership_id'))
    current = _current(case)
    with transaction.atomic():
        old = case.status
        case.status, case.outcome, case.closed_by, case.closed_at = CaseStatus.CLOSED, CaseOutcome.RESOLVED, admin, timezone.now()
        case.close_reason = comment or None
        case.save()
        CaseStatusHistory.objects.create(case=case, from_status=old, to_status=case.status, changed_by=admin,
                                         note='Closed by CLC' + (f': {comment[:300]}' if comment else ''))
        log_staff_action(request, 'case', case.id, 'case.closed', {'reference': case.reference, 'comment': bool(comment)})
        if current:
            Notification.objects.create(membership=current.advocate, channel=NotifyChannel.IN_APP, subject='A matter you handled was closed',
                                        body=case.reference, entity_type='case', entity_id=case.id)
    return Response(row(get_object_or_404(_queryset(), id=case_id)))


@admin_api(['DELETE'])
def delete_matter(request, case_id):
    """Permanently remove a matter with its documents, notes, referrals and history. The audit log keeps a record that it happened."""
    case = get_object_or_404(Case, id=case_id)
    reference = case.reference
    docs = list(case.documents.all())
    with transaction.atomic():
        log_staff_action(request, 'case', case.id, 'case.deleted', {'reference': reference, 'status': case.status})
        Notification.objects.filter(entity_type='case', entity_id=case.id).delete()
        referrals = Referral.objects.filter(case=case)
        Consent.objects.filter(referral__in=referrals).delete()
        ReferralDocument.objects.filter(referral__in=referrals).delete()
        referrals.delete()
        case.payments.all().delete()
        case.assignments.all().delete()
        case.status_history.all().delete()
        case.notes.all().delete()
        Document.objects.filter(case=case).delete()
        case.delete()  # the conversation stays; it just no longer points at a matter
    for d in docs:
        try:
            default_storage.delete(d.storage_key)
        except OSError:
            log.warning('Could not remove file %s of deleted matter %s', d.storage_key, reference)
    return Response({'deleted': reference})


def _email_advocate(advocate, case):
    """No client details: the advocate sees the matter, with the client's name, in the portal."""
    body = (f'Hello {advocate.user.full_name},\n\nA new matter was assigned to you on the CLC platform.\n\n'
            f'Reference: {case.reference}\nService: {case.service.name_en if case.service_id else "Legal matter"}\n'
            f'Region: {case.region or "-"}\n\nOpen it in your dashboard: {settings.FRONTEND_URL}/app/advocate/matters/{case.id}')
    try:
        EmailMessage(f'New matter assigned: {case.reference}', body, to=[advocate.user.email]).send()
    except Exception:  # a mail problem must never undo the assignment
        log.exception('Could not email advocate %s', advocate.user.email)
