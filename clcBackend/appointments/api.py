"""Appointment booking: public endpoints for the website pop-up, and the admin list."""
import re
import uuid
from datetime import datetime, timedelta

from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from accounts.models import MemberRole, Membership, MembershipStatus, OrgStatus
from accounts.permissions import admin_api, log_staff_action
from core.models import ReferenceCounter, ServicePackage
from django.db.models import F

from . import availability, emails
from .models import Appointment, AppointmentStatus, MeetingKind

GENERAL = 'General legal advice'


class BookingThrottle(AnonRateThrottle):
    scope = 'appointments'


def phone_e164(value):
    raw = re.sub(r'[\s\-().]', '', value or '')
    if raw.startswith('00'):
        raw = '+' + raw[2:]
    elif raw.startswith('0'):
        raw = '+255' + raw[1:]
    elif raw and not raw.startswith('+'):
        raw = '+' + raw
    return raw if re.fullmatch(r'\+\d{9,15}', raw) else None


def row(a):
    local = a.scheduled_at.astimezone(availability.TZ)
    return {
        'id': str(a.id), 'reference': a.reference, 'service': a.service, 'meeting_kind': a.meeting_kind,
        'meeting_label': a.get_meeting_kind_display(), 'date': local.date().isoformat(), 'time': local.strftime('%H:%M'),
        'scheduled_at': a.scheduled_at, 'full_name': a.full_name, 'phone': a.phone_e164, 'email': a.email,
        'language': a.language, 'note': a.note, 'status': a.status, 'status_note': a.status_note, 'created_at': a.created_at,
        'advocate_id': str(a.advocate_id) if a.advocate_id else None,
        'advocate_name': a.advocate.user.full_name if a.advocate_id else None,
    }


# ------------------------------------------------------------------ public

@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def options(request):
    titles = list(ServicePackage.objects.filter(is_published=True).values_list('title', flat=True))
    return Response({
        'services': titles, 'general': GENERAL,
        'meeting_kinds': [{'value': v, 'label': l} for v, l in MeetingKind.choices],
        'slot_minutes': availability.SLOT_MINUTES, 'days': availability.days_ahead(),
    })


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def slots(request):
    day = parse_date(request.GET.get('date', ''))
    if day is None:
        return Response({'detail': 'Choose a date.'}, status=400)
    return Response(availability.slots_for(day))


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([BookingThrottle])
def book(request):
    d = request.data
    errors = {}
    name = (d.get('full_name') or '').strip()
    if len(name) < 2:
        errors['full_name'] = 'Enter your full name.'
    phone = phone_e164(d.get('phone'))
    if not phone:
        errors['phone'] = 'Enter a valid phone number, e.g. +255 745 118 253.'
    mail = (d.get('email') or '').strip().lower()
    try:
        validate_email(mail)
    except ValidationError:
        errors['email'] = 'Enter a valid email address: your confirmation is sent there.'
    kind = d.get('meeting_kind')
    if kind not in MeetingKind.values:
        errors['meeting_kind'] = 'Choose how you want to meet.'
    service = (d.get('service') or '').strip()[:200] or GENERAL
    day = parse_date(str(d.get('date') or ''))
    try:
        hour, minute = (int(x) for x in str(d.get('time') or '').split(':'))
        moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=availability.TZ) if day else None
    except (ValueError, TypeError):
        moment = None
    if moment is None:
        errors['time'] = 'Choose a date and a time.'
    if errors:
        return Response({'detail': 'Please check your details.', 'errors': errors}, status=400)

    if not availability.is_bookable(moment):
        return Response({'detail': 'That time is no longer available. Please choose another time.', 'code': 'slot_taken'}, status=409)

    language = d.get('language') if d.get('language') in ('sw', 'en', 'zh') else 'sw'
    try:
        with transaction.atomic():
            year = timezone.localdate().year
            counter, _ = ReferenceCounter.objects.select_for_update().get_or_create(prefix='APT', year=year)
            ReferenceCounter.objects.filter(pk=counter.pk).update(last_value=F('last_value') + 1)
            counter.refresh_from_db()
            appt = Appointment.objects.create(
                reference=f'APT/{year}/{counter.last_value:04d}', service=service, meeting_kind=kind, scheduled_at=moment,
                duration_minutes=availability.SLOT_MINUTES, full_name=name, phone_e164=phone, email=mail, language=language,
                note=(d.get('note') or '').strip()[:1000])
    except IntegrityError:  # somebody else took the slot a moment ago
        return Response({'detail': 'That time was just taken. Please choose another time.', 'code': 'slot_taken'}, status=409)

    sent = emails.send_confirmation(appt)
    emails.send_admin_notice(appt)
    return Response({**row(appt), 'confirmation_emailed': sent}, status=201)


# ------------------------------------------------------------------ admin

@admin_api(['GET'])
def admin_list(request):
    qs = Appointment.objects.select_related('advocate__user')
    if request.GET.get('status'):
        qs = qs.filter(status=request.GET['status'])
    # upcoming first, then the past
    now = timezone.now()
    rows = sorted(qs, key=lambda a: (a.scheduled_at < now, abs((a.scheduled_at - now).total_seconds())))
    return Response([row(a) for a in rows])


@admin_api(['PATCH'])
def admin_update(request, appointment_id):
    a = Appointment.objects.select_related('advocate__user').filter(id=appointment_id).first()
    if a is None:
        return Response({'detail': 'Not found.'}, status=404)
    if 'advocate_id' in request.data:  # assign (or unassign) the advocate who takes this consultation
        wanted = request.data.get('advocate_id')
        try:
            wanted = uuid.UUID(str(wanted)) if wanted else None
        except ValueError:
            return Response({'detail': 'Choose an active advocate of an approved law firm.'}, status=400)
        if wanted:
            advocate = Membership.objects.select_related('user', 'organization').filter(
                id=wanted, role=MemberRole.ADVOCATE, status=MembershipStatus.ACTIVE, user__is_active=True,
                organization__status=OrgStatus.APPROVED).first()
            if advocate is None:
                return Response({'detail': 'Choose an active advocate of an approved law firm.'}, status=400)
            a.advocate = advocate
        else:
            a.advocate = None
        a.save()
        log_staff_action(request, 'appointment', a.id, 'appointment.advocate_assigned',
                         {'reference': a.reference, 'advocate': a.advocate.user.full_name if a.advocate_id else None})
        return Response(row(a))
    status = request.data.get('status')
    if status not in (AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
        return Response({'detail': 'Choose Completed, Cancelled or No show.'}, status=400)
    if a.status != AppointmentStatus.SCHEDULED:
        return Response({'detail': f'This appointment is already {a.get_status_display().lower()}.'}, status=409)
    a.status = status
    a.status_note = (request.data.get('note') or '').strip()[:500]
    a.save()
    log_staff_action(request, 'appointment', a.id, f'appointment.{status}', {'reference': a.reference})
    if status == AppointmentStatus.CANCELLED:
        emails.send_cancellation(a)
    return Response(row(a))
