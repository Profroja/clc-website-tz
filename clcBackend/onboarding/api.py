"""Law firm onboarding API.

Public (the applicant holds a secret link token, sent as X-Applicant-Token):
    options, create draft, read/update, upload/delete documents, submit.
CLC Admin (portal token, working as CLC Admin): review, meetings, agreements, approval, compliance.
"""
import functools
import os

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from accounts.permissions import admin_api

from . import services
from .models import (AppStatus, ApplicationAdvocate, ApplicationDocument, DocType, FirmApplication, MeetingOutcome,
                     MeetingType, OnboardingMeeting)
from .present import application_json, options_json, summary_json
from .serializers import ApplicationSerializer
from .services import ServiceError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}


class ApplyThrottle(AnonRateThrottle):
    scope = 'apply'


def handles_errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ServiceError as exc:
            return Response({'detail': exc.message, **exc.extra}, status=exc.status)
    return wrapper


def public_api(methods):
    def wrap(fn):
        fn = handles_errors(fn)
        return api_view(methods)(authentication_classes([])(permission_classes([AllowAny])(fn)))
    return wrap


def staff_api(methods):
    def wrap(fn):
        return admin_api(methods)(handles_errors(fn))
    return wrap


def applicant_app(request, app_id):
    app = get_object_or_404(FirmApplication, id=app_id)
    if not services.token_matches(app, request.headers.get('X-Applicant-Token', '')):
        raise ServiceError('This link is not valid.', 403)
    return app


def check_upload(upload):
    if upload is None:
        raise ServiceError('Choose a file to upload.', 400)
    if os.path.splitext(upload.name)[1].lower() not in ALLOWED_EXTENSIONS:
        raise ServiceError('Upload a PDF, JPG or PNG file.', 400)
    if upload.size > MAX_UPLOAD_BYTES:
        raise ServiceError('Files can be at most 10 MB.', 400)
    if upload.size == 0:
        raise ServiceError('That file is empty.', 400)


def parse_input(request):
    ser = ApplicationSerializer(data=request.data)
    if not ser.is_valid():
        raise ServiceError('Some answers need attention.', 400, {'errors': ser.errors})
    return ser.validated_data


# =============================== applicant ================================================

@public_api(['GET'])
def options(request):
    return Response(options_json())


@public_api(['POST'])
@throttle_classes([ApplyThrottle])
def create_application(request):
    data = parse_input(request)
    token, token_hash = services.make_token()
    app = FirmApplication.objects.create(token_hash=token_hash)
    services.save_data(app, data)
    return Response({'token': token, 'application': application_json(app)}, status=201)


@staff_api(['POST'])
def admin_create_application(request):
    """A CLC Admin starts an application on a firm's behalf. It then follows the same steps as a firm's own."""
    data = parse_input(request)
    token, token_hash = services.make_token()
    app = FirmApplication.objects.create(token_hash=token_hash, created_by_admin=True)
    services.save_data(app, data)
    return Response({'token': token, 'application': application_json(app)}, status=201)


@staff_api(['POST'])
def admin_submit_and_approve(request, app_id):
    """The admin who added a firm submits it: everything is accepted and the firm is approved (trial starts today)."""
    app = applicant_app(request, app_id)
    if app.status == AppStatus.DRAFT and not app.created_by_admin:  # started before this was an admin form (e.g. a page left open): the admin submitting it takes it on
        app.created_by_admin = True
        app.save(update_fields=['created_by_admin'])
    services.submit_and_approve(app, _membership_id(request), request)
    app.refresh_from_db()
    if app.email:
        services.email(app.email, f'CLC application {app.application_number}: approved',
                       f'{app.firm_name} has been added to the CLC platform and approved.\n\n'
                       f'Follow the details here:\n{services.tracking_link(app, request.headers.get("X-Applicant-Token"))}')
    return Response(application_json(app))


@public_api(['GET', 'PUT'])
def application_detail(request, app_id):
    app = applicant_app(request, app_id)
    if request.method == 'PUT':
        services.require_status(app, *services.EDITABLE)
        services.save_data(app, parse_input(request))
        app.refresh_from_db()
    return Response(application_json(app))


@public_api(['POST'])
def upload_document(request, app_id):
    app = applicant_app(request, app_id)
    services.require_status(app, *services.EDITABLE)
    doc_type = request.data.get('doc_type')
    if doc_type not in DocType.values:
        raise ServiceError('Unknown document type.', 400)
    upload = request.FILES.get('file')
    check_upload(upload)
    advocate = None
    if request.data.get('advocate_id'):
        advocate = get_object_or_404(ApplicationAdvocate, id=request.data['advocate_id'], application=app)
    doc = services.add_document(app, doc_type, upload, advocate)
    services.audit(app, 'document.uploaded', request, {'type': doc_type, 'file': doc.file_name})
    return Response(application_json(app), status=201)


@public_api(['DELETE'])
def delete_document(request, app_id, doc_id):
    app = applicant_app(request, app_id)
    services.require_status(app, *services.EDITABLE)
    doc = get_object_or_404(ApplicationDocument, id=doc_id, application=app)
    if doc.status == 'accepted':
        raise ServiceError('CLC has already accepted this document.')
    doc.file.delete(save=False)
    doc.delete()
    return Response(application_json(app))


@public_api(['POST'])
def submit_application(request, app_id):
    app = applicant_app(request, app_id)
    first_time = app.status == AppStatus.DRAFT
    token = request.headers.get('X-Applicant-Token')
    services.submit(app, request)
    if first_time and app.email:
        services.email(
            app.email, f'CLC application received: {app.application_number}',
            f'Thank you for applying to join CLC.\n\nYour application number is {app.application_number}.\n'
            f'Follow its progress and answer any requests from CLC here:\n{services.tracking_link(app, token)}\n\n'
            f'Asante kwa kuomba kujiunga na CLC. Nambari ya maombi yako ni {app.application_number}.')
    return Response(application_json(app))


@public_api(['GET'])
def applicant_download(request, app_id, doc_id):
    app = applicant_app(request, app_id)
    doc = get_object_or_404(ApplicationDocument, id=doc_id, application=app)
    return FileResponse(doc.file.open('rb'), as_attachment=True, filename=doc.file_name)


# ================================ CLC admin ===============================================

@staff_api(['GET'])
def admin_list(request):
    qs = FirmApplication.objects.exclude(status=AppStatus.DRAFT).order_by('-submitted_at')
    if request.GET.get('status'):
        qs = qs.filter(status=request.GET['status'])
    return Response([summary_json(a) for a in qs])


def _app(app_id):
    return get_object_or_404(FirmApplication.objects.exclude(status=AppStatus.DRAFT), id=app_id)


def _membership_id(request):
    return request.auth.get('membership_id')


@staff_api(['GET'])
def admin_detail(request, app_id):
    return Response(application_json(_app(app_id), admin=True))


def _result(app):
    app.refresh_from_db()
    return Response(application_json(app, admin=True))


@staff_api(['POST'])
def admin_start_review(request, app_id):
    app = _app(app_id)
    services.start_review(app, _membership_id(request), request)
    return _result(app)


@staff_api(['POST'])
def admin_review_document(request, app_id, doc_id):
    app = _app(app_id)
    doc = get_object_or_404(ApplicationDocument, id=doc_id, application=app)
    services.review_document(app, doc, request.data.get('decision'), (request.data.get('note') or '').strip(),
                             _membership_id(request), request)
    return _result(app)


@staff_api(['POST'])
def admin_request_changes(request, app_id):
    app = _app(app_id)
    advocate = None
    if request.data.get('advocate_id'):
        advocate = get_object_or_404(ApplicationAdvocate, id=request.data['advocate_id'], application=app)
    services.request_changes(app, request.data.get('kind'), request.data.get('message') or '', _membership_id(request),
                             request, doc_type=request.data.get('doc_type'), advocate=advocate)
    return _result(app)


@staff_api(['POST'])
def admin_complete_review(request, app_id):
    app = _app(app_id)
    services.complete_review(app, request)
    return _result(app)


@staff_api(['POST'])
def admin_add_meeting(request, app_id):
    app = _app(app_id)
    services.require_status(app, AppStatus.UNDER_REVIEW, AppStatus.MEETING_PENDING, AppStatus.AGREEMENTS_PENDING)
    when = parse_datetime(str(request.data.get('scheduled_at') or ''))
    if when is None:
        raise ServiceError('Give the meeting date and time.', 400)
    if timezone.is_naive(when):
        when = timezone.make_aware(when)
    if request.data.get('meeting_type') not in MeetingType.values:
        raise ServiceError('Choose physical or online.', 400)
    OnboardingMeeting.objects.create(
        application=app, scheduled_at=when, meeting_type=request.data['meeting_type'],
        location_or_link=request.data.get('location_or_link') or '', participants=request.data.get('participants') or '',
        recorded_by_id=_membership_id(request))
    services.audit(app, 'meeting.scheduled', request, {'at': str(when)})
    return _result(app)


@staff_api(['PATCH'])
def admin_update_meeting(request, app_id, meeting_id):
    app = _app(app_id)
    services.require_status(app, AppStatus.UNDER_REVIEW, AppStatus.MEETING_PENDING, AppStatus.AGREEMENTS_PENDING)
    meeting = get_object_or_404(OnboardingMeeting, id=meeting_id, application=app)
    for field in ('participants', 'notes', 'location_or_link'):
        if field in request.data:
            setattr(meeting, field, request.data[field] or '')
    if isinstance(request.data.get('topics'), list):
        meeting.topics = [str(t) for t in request.data['topics']]
    if 'outcome' in request.data:
        if request.data['outcome'] not in MeetingOutcome.values:
            raise ServiceError('Unknown meeting outcome.', 400)
        meeting.outcome = request.data['outcome']
    meeting.save()
    services.audit(app, 'meeting.updated', request, {'outcome': meeting.outcome})
    services.meeting_changed(app, request)
    return _result(app)


@staff_api(['POST'])
def admin_upload_agreement(request, app_id, agreement_type):
    app = _app(app_id)
    upload = request.FILES.get('file')
    check_upload(upload)
    services.upload_agreement(app, agreement_type, upload, _membership_id(request), request)
    return _result(app)


@staff_api(['GET'])
def admin_download_agreement(request, app_id, agreement_type):
    app = _app(app_id)
    ag = get_object_or_404(app.agreements, agreement_type=agreement_type)
    if not ag.file:
        raise ServiceError('No file has been uploaded for this agreement.', 404)
    return FileResponse(ag.file.open('rb'), as_attachment=True, filename=ag.file_name)

@staff_api(['POST'])
def admin_approve(request, app_id):
    app = _app(app_id)
    services.approve(app, _membership_id(request), request,
                     trial_start=parse_date(str(request.data.get('trial_start') or '')),
                     billing_effective=parse_date(str(request.data.get('billing_effective') or '')))
    return _result(app)


@staff_api(['POST'])
def admin_reject(request, app_id):
    app = _app(app_id)
    services.reject(app, request.data.get('reason') or '', request)
    return _result(app)


@staff_api(['POST'])
def admin_suspend(request, app_id):
    app = _app(app_id)
    services.suspend(app, request.data.get('reason') or '', request)
    return _result(app)


@staff_api(['POST'])
def admin_reinstate(request, app_id):
    app = _app(app_id)
    services.reinstate(app, request)
    return _result(app)


@staff_api(['POST'])
def admin_activate(request, app_id):
    app = _app(app_id)
    services.activate(app, request, parse_date(str(request.data.get('billing_effective') or '')))
    return _result(app)


@staff_api(['GET'])
def admin_download(request, app_id, doc_id):
    app = _app(app_id)
    doc = get_object_or_404(ApplicationDocument, id=doc_id, application=app)
    services.audit(app, 'document.downloaded', request, {'document': str(doc.id)})
    return FileResponse(doc.file.open('rb'), as_attachment=True, filename=doc.file_name)


@staff_api(['GET'])
def admin_compliance(request):
    return Response(services.expiring_items(int(request.GET.get('days') or services.EXPIRY_WARNING_DAYS)))
