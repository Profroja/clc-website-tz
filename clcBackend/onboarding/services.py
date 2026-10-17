"""Business rules of the law firm onboarding workflow. API views stay thin and call these."""
import calendar
import hashlib
import logging
import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import (AdvocateProfile, MemberRole, Membership, MembershipStatus, Organization, OrgStatus,
                             OrgType, User)
from accounts.services import temp_password
from core.models import ActorType, ReferenceCounter
from notifications.models import Notification, NotifyChannel
from tracking.models import ActivityEvent

from .models import (Agreement, AgreementType, AppStatus, ApplicationAdvocate, ApplicationDocument, DocStatus,
                     DocType, FirmApplication, FirmExpertise, FirmPartner, MeetingOutcome, MULTI_FILE_DOCS,
                     PracticeArea, REQUIRED_FIRM_DOCS, RequestKind, ReviewRequest)

log = logging.getLogger('onboarding')

EDITABLE = {AppStatus.DRAFT, AppStatus.CORRECTION_REQUIRED}
IN_REVIEW = {AppStatus.UNDER_REVIEW, AppStatus.CORRECTION_REQUIRED, AppStatus.MEETING_PENDING,
             AppStatus.AGREEMENTS_PENDING}
CAN_BE_REJECTED = {AppStatus.SUBMITTED} | IN_REVIEW
TRIAL_MONTHS = 2
EXPIRY_WARNING_DAYS = 60


class ServiceError(Exception):
    def __init__(self, message, status=409, extra=None):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra or {}


def require_status(app, *allowed):
    if app.status not in allowed:
        raise ServiceError(f'This cannot be done while the application is "{app.get_status_display()}".')


# --- audit and notifications ---------------------------------------------------------

def audit(app, action, request=None, after=None):
    """Audit trail entry. Admin actions are logged under the role they were done as; the
    applicant's own actions are logged as system events."""
    claims = getattr(request, 'auth', None)
    if claims is not None and claims.get('session_id'):
        kwargs = dict(actor_type=ActorType.STAFF, session_id=claims.get('session_id'),
                      acting_membership_id=claims.get('membership_id'))
    else:
        kwargs = dict(actor_type=ActorType.SYSTEM)
        after = {**(after or {}), 'by': 'applicant'} if request is not None else after
    ActivityEvent.objects.create(entity_type='firm_application', entity_id=app.id, action=action,
                                 after_data=after, **kwargs)


def notify_admins(app, subject, body):
    for m in Membership.objects.filter(role=MemberRole.CLC_ADMIN, status=MembershipStatus.ACTIVE):
        Notification.objects.create(membership=m, channel=NotifyChannel.IN_APP, subject=subject, body=body,
                                    entity_type='firm_application', entity_id=app.id)


def tracking_link(app, token):
    return f'{settings.FRONTEND_URL}/#/join/track/{app.id}/{token}'


def email(to, subject, body):
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to], fail_silently=False)
    except Exception:  # an unreachable mail server must never block the workflow
        log.exception('Could not send email to %s', to)


# --- tokens, numbers, dates ----------------------------------------------------------

def make_token():
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def token_matches(app, token):
    return bool(token) and secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), app.token_hash)


def next_application_number():
    year = timezone.localdate().year
    counter, _ = ReferenceCounter.objects.select_for_update().get_or_create(prefix='CLC-LF', year=year)
    ReferenceCounter.objects.filter(pk=counter.pk).update(last_value=F('last_value') + 1)
    counter.refresh_from_db()
    return f'CLC-LF/{year}/{counter.last_value:04d}'


def add_months(d, n):
    year, month0 = divmod(d.month - 1 + n, 12)
    year, month = d.year + year, month0 + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


# --- saving the form -----------------------------------------------------------------

SCALAR_FIELDS = ['firm_name', 'brela_number', 'registration_date', 'tin', 'licence_number', 'licence_expiry',
                 'region', 'district', 'ward', 'street', 'postal_address', 'map_location', 'phone_e164',
                 'whatsapp_e164', 'email', 'website', 'contact_person', 'contact_person_phone', 'other_areas',
                 'regions_served', 'languages']


@transaction.atomic
def save_data(app, data):
    for field in SCALAR_FIELDS:
        if field in data:
            setattr(app, field, data[field])
    app.save()

    if 'practice_areas' in data:
        areas = {a.code: a for a in PracticeArea.objects.filter(code__in=[x['code'] for x in data['practice_areas']])}
        app.expertise.all().delete()
        FirmExpertise.objects.bulk_create([FirmExpertise(application=app, area=areas[x['code']], is_primary=x['is_primary'])
                                           for x in data['practice_areas']])

    if 'partners' in data:
        keep = []
        for p in data['partners']:
            fields = {k: v for k, v in p.items() if k != 'id'}
            existing = app.partners.filter(id=p['id']).first() if p.get('id') else None
            if existing:
                for k, v in fields.items():
                    setattr(existing, k, v)
                existing.save()
            else:
                existing = FirmPartner(application=app, **fields)
                existing.is_authorized_rep = False  # set below, after the old rep is cleared
                existing.save()
            keep.append((existing.id, p['is_authorized_rep']))
        app.partners.exclude(id__in=[k for k, _ in keep]).delete()
        app.partners.update(is_authorized_rep=False)
        for pid, is_rep in keep:
            if is_rep:
                app.partners.filter(id=pid).update(is_authorized_rep=True)

    if 'advocates' in data:
        kept = []
        for a in data['advocates']:
            codes = a.pop('practice_areas', [])
            fields = {k: v for k, v in a.items() if k != 'id'}
            existing = app.advocates.filter(id=a['id']).first() if a.get('id') else None
            if existing:
                for k, v in fields.items():
                    setattr(existing, k, v)
            else:
                existing = ApplicationAdvocate(application=app, **fields)
            existing.save()
            existing.practice_areas.set(PracticeArea.objects.filter(code__in=codes))
            kept.append(existing.id)
        app.advocates.exclude(id__in=kept).delete()  # their documents go with them
    return app


# --- documents -----------------------------------------------------------------------

def add_document(app, doc_type, upload, advocate=None):
    """Store an upload. For single-file types a new upload replaces the previous one."""
    if doc_type == DocType.PRACTISING_CERTIFICATE and advocate is None:
        raise ServiceError('Say which advocate this practising certificate belongs to.', 400)
    sha = hashlib.sha256()
    for chunk in upload.chunks():
        sha.update(chunk)
    upload.seek(0)
    with transaction.atomic():
        if doc_type not in MULTI_FILE_DOCS:
            app.documents.filter(doc_type=doc_type, advocate=advocate, is_current=True).update(is_current=False)
        doc = ApplicationDocument.objects.create(
            application=app, advocate=advocate, doc_type=doc_type, file=upload, file_name=upload.name[:200],
            mime_type=upload.content_type or 'application/octet-stream', size_bytes=upload.size,
            sha256=sha.hexdigest(),
        )
    return doc


def current_docs(app):
    return list(app.documents.filter(is_current=True).select_related('advocate'))


def missing_documents(app):
    docs = current_docs(app)
    have = {(d.doc_type, d.advocate_id) for d in docs}
    missing = [DocType(t).label for t in REQUIRED_FIRM_DOCS if (t, None) not in have]
    for a in app.advocates.all():
        if (DocType.PRACTISING_CERTIFICATE, a.id) not in have:
            missing.append(f'Practising Certificate: {a.full_name}')
    return missing


# --- submission ----------------------------------------------------------------------

def submission_problems(app):
    p = []
    for label, value in [('Registered firm name', app.firm_name), ('BRELA registration number', app.brela_number),
                         ('Registration date', app.registration_date), ('TIN', app.tin),
                         ('Business licence number', app.licence_number), ('Business licence expiry date', app.licence_expiry),
                         ('Region', app.region), ('District', app.district), ('Ward', app.ward),
                         ('Street / building', app.street), ('Official phone', app.phone_e164),
                         ('Official email', app.email), ('Contact person', app.contact_person)]:
        if not value:
            p.append(f'{label} is required.')
    partners = list(app.partners.all())
    rep = next((x for x in partners if x.is_authorized_rep), None)
    if not app.created_by_admin:  # a CLC Admin may leave partners out; the firm admin account is made on the Users page
        if not partners:
            p.append('Add at least one partner or proprietor.')
        if not any(x.is_managing_partner for x in partners):
            p.append('Mark the Managing Partner / Principal.')
        if partners and rep is None:
            p.append('Choose the authorized representative.')
    if rep and not rep.email:
        p.append('The authorized representative needs an email address.')
    if not app.expertise.exists():
        p.append('Choose at least one area of expertise.')
    elif not app.expertise.filter(is_primary=True).exists():
        p.append('Choose 1 to 5 primary areas of expertise.')
    if not app.regions_served:
        p.append('Select the regions you serve.')
    if not app.languages:
        p.append('Select the languages you support.')
    advocates = list(app.advocates.all())
    if not advocates and not app.created_by_admin:
        p.append('Add at least one advocate who will handle CLC cases.')
    for a in advocates:
        if not (a.practising_certificate and a.certificate_expiry):
            p.append(f'{a.full_name}: practising certificate number and expiry date are required.')
        if not a.practice_areas.exists() and not a.other_areas:
            p.append(f'{a.full_name}: choose at least one area of expertise.')
    if not app.created_by_admin:  # a CLC Admin may add a firm without its documents
        p += [f'Upload: {m}' for m in missing_documents(app)]
    return p


@transaction.atomic
def submit(app, request=None):
    """Draft -> Submitted. Correction required -> Under review (resubmission)."""
    require_status(app, *EDITABLE)
    problems = submission_problems(app)
    if problems:
        raise ServiceError('Please complete the application before submitting.', 400, {'problems': problems})
    now = timezone.now()
    if app.status == AppStatus.DRAFT:
        app.application_number = next_application_number()
        org = Organization.objects.create(
            type=OrgType.LAW_FIRM, name=app.firm_name, registration_number=app.brela_number, tin=app.tin,
            region=app.region, regions_served=app.regions_served, email=app.email, phone_e164=app.phone_e164,
            address=', '.join(x for x in [app.street, app.ward, app.district, app.region] if x),
            status=OrgStatus.PENDING)
        app.organization = org
        app.submitted_at = now
        app.status = AppStatus.SUBMITTED
        for t in AgreementType.values:
            Agreement.objects.get_or_create(application=app, agreement_type=t)
        action, subject = 'application.submitted', f'New law firm application: {app.firm_name}'
    else:
        app.requests.filter(resolved_at__isnull=True).update(resolved_at=now)
        app.status = AppStatus.UNDER_REVIEW
        app.status_reason = None
        action, subject = 'application.resubmitted', f'Corrections submitted: {app.firm_name}'
    app.save()
    audit(app, action, request, {'application_number': app.application_number})
    notify_admins(app, subject, f'{app.application_number} - {app.firm_name}')
    return app


# --- CLC review ----------------------------------------------------------------------

@transaction.atomic
def start_review(app, membership_id, request):
    require_status(app, AppStatus.SUBMITTED)
    app.status, app.reviewer_id, app.review_started_at = AppStatus.UNDER_REVIEW, membership_id, timezone.now()
    app.save()
    audit(app, 'application.review_started', request)


@transaction.atomic
def review_document(app, doc, decision, note, membership_id, request):
    require_status(app, AppStatus.UNDER_REVIEW, AppStatus.MEETING_PENDING, AppStatus.AGREEMENTS_PENDING)
    if not doc.is_current:
        raise ServiceError('This document has been replaced by a newer upload.')
    if decision == 'accept':
        doc.status, doc.reviewer_note = DocStatus.ACCEPTED, note or ''
    elif decision == 'request_replacement':
        if not note:
            raise ServiceError('Explain what is wrong with the document.', 400)
        doc.status, doc.reviewer_note = DocStatus.REPLACEMENT_REQUESTED, note
    else:
        raise ServiceError('decision must be "accept" or "request_replacement".', 400)
    doc.reviewed_by_id, doc.reviewed_at = membership_id, timezone.now()
    doc.save()
    audit(app, f'document.{decision}', request, {'document': str(doc.id), 'type': doc.doc_type})
    return doc


@transaction.atomic
def request_changes(app, kind, message, membership_id, request, doc_type=None, advocate=None):
    """Ask the firm for a correction or a replacement/additional document."""
    require_status(app, *IN_REVIEW)
    if kind not in RequestKind.values or not message.strip():
        raise ServiceError('Write what the firm needs to fix or provide.', 400)
    if kind == RequestKind.DOCUMENT and doc_type not in DocType.values:
        raise ServiceError('Say which document is needed.', 400)
    req = ReviewRequest.objects.create(application=app, kind=kind, message=message.strip(), created_by_id=membership_id,
                                       doc_type=doc_type if kind == RequestKind.DOCUMENT else None, advocate=advocate)
    app.status = AppStatus.CORRECTION_REQUIRED
    app.save()
    audit(app, 'application.correction_requested', request, {'kind': kind, 'message': message})
    if app.email:
        email(app.email, f'CLC application {app.application_number}: action needed',
              f'CLC has asked you to update your application.\n\n{message}\n\nOpen your application and resubmit.')
    return req


def review_blockers(app):
    """What still stops the review from being completed."""
    blockers = [f'Missing: {m}' for m in missing_documents(app)]
    for d in current_docs(app):
        if d.doc_type in REQUIRED_FIRM_DOCS or d.doc_type == DocType.PRACTISING_CERTIFICATE:
            if d.status != DocStatus.ACCEPTED:
                who = f' ({d.advocate.full_name})' if d.advocate else ''
                blockers.append(f'{DocType(d.doc_type).label}{who} is not accepted yet.')
    if app.requests.filter(resolved_at__isnull=True).exists():
        blockers.append('There are open requests to the firm.')
    return blockers


@transaction.atomic
def complete_review(app, request):
    require_status(app, AppStatus.UNDER_REVIEW)
    blockers = review_blockers(app)
    if blockers:
        raise ServiceError('The review cannot be completed yet.', 409, {'problems': blockers})
    app.status = AppStatus.AGREEMENTS_PENDING if has_completed_meeting(app) else AppStatus.MEETING_PENDING
    app.save()
    audit(app, 'application.review_completed', request)


def has_completed_meeting(app):
    return app.meetings.filter(outcome=MeetingOutcome.COMPLETED).exists()


@transaction.atomic
def meeting_changed(app, request):
    """A recorded meeting moves a firm on to agreements."""
    if app.status == AppStatus.MEETING_PENDING and has_completed_meeting(app):
        app.status = AppStatus.AGREEMENTS_PENDING
        app.save()
        audit(app, 'application.meeting_completed', request)


@transaction.atomic
def upload_agreement(app, agreement_type, upload, membership_id, request):
    """The signed agreement is a document the admin uploads; uploading it marks the agreement as done."""
    require_status(app, AppStatus.AGREEMENTS_PENDING)
    if agreement_type not in AgreementType.values:
        raise ServiceError('Unknown agreement.', 400)
    ag = app.agreements.get(agreement_type=agreement_type)
    if ag.file:
        ag.file.delete(save=False)
    ag.file, ag.file_name = upload, upload.name[:200]
    ag.signed_at, ag.recorded_by_id = timezone.localdate(), membership_id
    ag.save()
    audit(app, 'agreement.uploaded', request, {'type': agreement_type, 'file': ag.file_name})
    return ag

# --- approval and what follows --------------------------------------------------------

def approval_blockers(app):
    b = []
    if not app.created_by_admin:  # a firm a CLC Admin added needs no review, meeting or agreements
        if app.status != AppStatus.AGREEMENTS_PENDING:
            b.append('Documents, meeting and agreements must be completed first.')
        b += review_blockers(app)
        if not has_completed_meeting(app):
            b.append('The pre-approval meeting has not been completed.')
        for ag in app.agreements.all():
            if not ag.file:
                b.append(f'{AgreementType(ag.agreement_type).label}: signed copy not uploaded.')
    for a in app.advocates.all():
        if AdvocateProfile.objects.filter(roll_number__iexact=a.roll_number).exclude(user=a.user).exists():
            b.append(f'Roll number {a.roll_number} already belongs to another advocate on the platform.')
    return list(dict.fromkeys(b))


def _create_login(app, membership_admin, full_name, mail, phone, role, organization):
    """Create (or reuse) the user, give them the role and email a temporary password."""
    user = User.objects.filter(email__iexact=mail).first()
    temp = None
    if user is None:
        temp = temp_password()
        user = User(email=mail, full_name=full_name, phone_e164=phone or None, must_change_password=True)
        user.set_password(temp)
        user.save()
    Membership.objects.get_or_create(user=user, organization=organization, role=role,
                                     defaults={'status': MembershipStatus.ACTIVE, 'invited_by': membership_admin})
    Membership.objects.filter(user=user, organization=organization, role=role).update(status=MembershipStatus.ACTIVE)
    role_name = 'Law Firm Admin' if role == MemberRole.FIRM_ADMIN else 'Advocate'
    if temp:
        email(mail, 'Your CLC platform account',
              f'Hello {full_name},\n\n{app.firm_name} has been approved on the CLC platform. Your {role_name} account:\n\n'
              f'Username: {mail}\nTemporary password: {temp}\nSign in: {settings.FRONTEND_URL}/#/login\n\n'
              f'You will be asked to choose a new password the first time you sign in.\n\n'
              f'Habari {full_name}, {app.firm_name} imeidhinishwa kwenye jukwaa la CLC. '
              f'Tumia taarifa hapo juu kuingia; utaombwa kuweka nenosiri jipya.')
    else:
        email(mail, 'New CLC role added',
              f'Hello {full_name},\n\nYou now also work as {role_name} for {app.firm_name}. Sign in: {settings.FRONTEND_URL}/#/login')
    return user


@transaction.atomic
def approve(app, membership_id, request, trial_start=None, billing_effective=None):
    blockers = approval_blockers(app)
    if blockers:
        raise ServiceError('This firm cannot be approved yet.', 409, {'problems': blockers})
    admin_m = Membership.objects.get(id=membership_id)
    org, today = app.organization, timezone.localdate()
    trial_start = trial_start or today
    trial_end = add_months(trial_start, TRIAL_MONTHS)

    org.status, org.approved_at, org.status_reason = OrgStatus.APPROVED, timezone.now(), None
    org.save()
    app.status, app.decided_at = AppStatus.APPROVED_FOR_TRIAL, timezone.now()
    app.trial_start, app.trial_end = trial_start, trial_end
    app.billing_effective = billing_effective or trial_end  # billing starts after the trial unless CLC sets another date
    app.save()

    # Only the advocates' accounts are created automatically; the firm admin is created by a CLC Admin on the Users page.
    for a in app.advocates.all():
        user = _create_login(app, admin_m, a.full_name, a.email.lower(), a.phone_e164, MemberRole.ADVOCATE, org)
        AdvocateProfile.objects.update_or_create(user=user, defaults={
            'roll_number': a.roll_number, 'practising_certificate': a.practising_certificate,
            'certificate_expires_on': a.certificate_expiry, 'verified_at': timezone.now(), 'verified_by': admin_m})
        profile = AdvocateProfile.objects.get(user=user)
        profile.practice_areas.set(a.practice_areas.all())
        profile.other_areas = a.other_areas
        profile.save(update_fields=['other_areas'])
        a.user = user
        a.save(update_fields=['user'])
    audit(app, 'application.approved', request, {'trial_start': str(app.trial_start), 'trial_end': str(app.trial_end),
                                                 'billing_effective': str(app.billing_effective)})
    return app


@transaction.atomic
def submit_and_approve(app, membership_id, request):
    """A firm a CLC Admin added: submit it, accept its documents and approve it in one go (the admin is the reviewer)."""
    if not app.created_by_admin:
        raise ServiceError('Only a firm added by a CLC Admin is approved straight away.', 403)
    app = submit(app, request)
    now = timezone.now()
    app.documents.filter(is_current=True).update(status=DocStatus.ACCEPTED, reviewed_by_id=membership_id, reviewed_at=now)
    app.reviewer_id, app.review_started_at = membership_id, now
    app.save()
    audit(app, 'application.auto_reviewed', request, {'by': 'CLC Admin who added the firm'})
    return approve(app, membership_id, request)


@transaction.atomic
def reject(app, reason, request):
    require_status(app, *CAN_BE_REJECTED)
    if not reason.strip():
        raise ServiceError('Give the reason for rejecting this application.', 400)
    app.status, app.status_reason, app.decided_at = AppStatus.REJECTED, reason.strip(), timezone.now()
    app.save()
    if app.organization_id:
        Organization.objects.filter(id=app.organization_id).update(status=OrgStatus.REJECTED, status_reason=reason.strip())
    audit(app, 'application.rejected', request, {'reason': reason})
    if app.email:
        email(app.email, f'CLC application {app.application_number}', f'Your application was not approved.\n\n{reason}')


@transaction.atomic
def suspend(app, reason, request):
    require_status(app, AppStatus.APPROVED_FOR_TRIAL, AppStatus.ACTIVE)
    if not reason.strip():
        raise ServiceError('Give the reason for suspending this firm.', 400)
    app.status_before_suspension, app.status, app.status_reason = app.status, AppStatus.SUSPENDED, reason.strip()
    app.save()
    Organization.objects.filter(id=app.organization_id).update(status=OrgStatus.SUSPENDED, status_reason=reason.strip())
    audit(app, 'application.suspended', request, {'reason': reason})


@transaction.atomic
def reinstate(app, request):
    require_status(app, AppStatus.SUSPENDED)
    app.status, app.status_reason, app.status_before_suspension = (
        app.status_before_suspension or AppStatus.ACTIVE, None, None)
    app.save()
    Organization.objects.filter(id=app.organization_id).update(status=OrgStatus.APPROVED, status_reason=None)
    audit(app, 'application.reinstated', request)


@transaction.atomic
def activate(app, request, billing_effective=None):
    """End of the trial: the firm becomes Active and any agreed financial terms start."""
    require_status(app, AppStatus.APPROVED_FOR_TRIAL)
    app.status = AppStatus.ACTIVE
    if billing_effective:
        app.billing_effective = billing_effective
    app.save()
    audit(app, 'application.activated', request)


# --- compliance ----------------------------------------------------------------------

def expiring_items(days=EXPIRY_WARNING_DAYS):
    """Business licences and practising certificates that expire soon or already have."""
    today = timezone.localdate()
    limit = today + timedelta(days=days)
    items = []
    for app in FirmApplication.objects.filter(status__in=[AppStatus.APPROVED_FOR_TRIAL, AppStatus.ACTIVE],
                                              licence_expiry__lte=limit):
        items.append(('business_licence', app.firm_name, app.firm_name, app.licence_expiry, app.id))
    for prof in AdvocateProfile.objects.filter(certificate_expires_on__lte=limit, verified_at__isnull=False):
        firm = Membership.objects.filter(user=prof.user, role=MemberRole.ADVOCATE,
                                         organization__type=OrgType.LAW_FIRM).select_related('organization').first()
        app = getattr(firm.organization, 'application', None) if firm else None
        if app and app.status in (AppStatus.APPROVED_FOR_TRIAL, AppStatus.ACTIVE):
            items.append(('practising_certificate', prof.user.full_name, app.firm_name, prof.certificate_expires_on, app.id))
    return [{'kind': k, 'name': n, 'firm': f, 'expires_on': d, 'days_left': (d - today).days, 'expired': d < today,
             'application_id': a} for k, n, f, d, a in sorted(items, key=lambda x: x[3])]
