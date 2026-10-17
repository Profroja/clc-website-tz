"""Emails about appointments: a confirmation to the visitor, a notice to the CLC admins, and a cancellation notice."""
import logging

from django.conf import settings
from django.core.mail import EmailMessage

from accounts.models import MemberRole, MembershipStatus, User

from .availability import TZ
from .models import MeetingKind

log = logging.getLogger('appointments')

OFFICE = 'Community Legal Clinic, P.O. Box 4661, Dar es Salaam'
CLC_WHATSAPP = '+255 745 118 253'

KIND = {
    'sw': {'whatsapp': 'Simu ya WhatsApp', 'phone': 'Simu ya kawaida', 'google_meet': 'Google Meet', 'in_person': 'Ana kwa ana ofisini'},
    'en': {'whatsapp': 'WhatsApp call', 'phone': 'Phone call', 'google_meet': 'Google Meet', 'in_person': 'In person'},
}
HOW = {
    'sw': {
        'whatsapp': 'Tutakupigia kwa WhatsApp kupitia namba {phone} muda wa appointment.',
        'phone': 'Tutakupigia kwa simu kupitia namba {phone} muda wa appointment.',
        'google_meet': 'Tutakutumia kiungo cha Google Meet kwa barua pepe kabla ya appointment.',
        'in_person': 'Tafadhali fika ofisini kwetu: ' + OFFICE + '.',
    },
    'en': {
        'whatsapp': 'We will call you on WhatsApp at {phone} at the appointment time.',
        'phone': 'We will phone you on {phone} at the appointment time.',
        'google_meet': 'We will email you a Google Meet link before the appointment.',
        'in_person': 'Please come to our office: ' + OFFICE + '.',
    },
}


def lang_of(appt):
    return 'sw' if appt.language == 'sw' else 'en'  # Chinese visitors get English


def when(appt):
    local = appt.scheduled_at.astimezone(TZ)
    return local.strftime('%A %d %B %Y'), local.strftime('%H:%M')


def admin_recipients():
    emails = list(User.objects.filter(is_active=True, memberships__role=MemberRole.CLC_ADMIN,
                                      memberships__status=MembershipStatus.ACTIVE).values_list('email', flat=True).distinct())
    return sorted({e.lower() for e in emails + list(settings.APPOINTMENT_NOTIFY_EMAILS)})


def _send(message):
    try:
        message.send(fail_silently=False)
        return True
    except Exception:  # a mail problem must never undo a booking
        log.exception('Could not send appointment email to %s', message.to)
        return False


def send_confirmation(appt):
    """To the visitor: the booking, and what happens next."""
    lang = lang_of(appt)
    day, hour = when(appt)
    kind = KIND[lang][appt.meeting_kind]
    how = HOW[lang][appt.meeting_kind].format(phone=appt.phone_e164)
    if lang == 'sw':
        subject = f'Appointment yako na CLC imethibitishwa — {appt.reference}'
        body = (f'Habari {appt.full_name},\n\nAppointment yako na Community Legal Clinic imepangwa.\n\n'
                f'Namba ya appointment: {appt.reference}\nHuduma: {appt.service}\nNjia: {kind}\n'
                f'Tarehe: {day}\nSaa: {hour} (Saa za Afrika Mashariki)\n\n{how}\n\n'
                f'Ukihitaji kubadilisha au kughairi appointment, tuandikie WhatsApp {CLC_WHATSAPP} ukitaja namba ya appointment.\n\n'
                f'Karibu sana,\nCommunity Legal Clinic')
    else:
        subject = f'Your CLC appointment is confirmed — {appt.reference}'
        body = (f'Hello {appt.full_name},\n\nYour appointment with Community Legal Clinic is scheduled.\n\n'
                f'Reference: {appt.reference}\nService: {appt.service}\nMeeting: {kind}\n'
                f'Date: {day}\nTime: {hour} (East Africa Time)\n\n{how}\n\n'
                f'To change or cancel, message us on WhatsApp {CLC_WHATSAPP} and quote your reference.\n\n'
                f'Kind regards,\nCommunity Legal Clinic')
    return _send(EmailMessage(subject, body, to=[appt.email]))


def send_admin_notice(appt):
    """To the CLC admins: someone booked."""
    recipients = admin_recipients()
    if not recipients:
        return False
    day, hour = when(appt)
    body = (f'A new appointment was booked on the website.\n\n'
            f'Reference: {appt.reference}\nWhen: {day}, {hour} (EAT)\nMeeting: {KIND["en"][appt.meeting_kind]}\n'
            f'Service: {appt.service}\n\nClient: {appt.full_name}\nPhone: {appt.phone_e164}\nEmail: {appt.email}\n'
            f'Language: {appt.language}\n' + (f'\nNote from the client:\n{appt.note}\n' if appt.note else '') +
            f'\nSee all appointments: {settings.FRONTEND_URL}/#/app/admin/appointments')
    return _send(EmailMessage(f'New appointment: {appt.full_name} — {day}, {hour}', body, to=recipients, reply_to=[appt.email]))


def send_cancellation(appt):
    """To the visitor: CLC cancelled the appointment."""
    lang = lang_of(appt)
    day, hour = when(appt)
    reason = f'\n{appt.status_note}\n' if appt.status_note else ''
    if lang == 'sw':
        subject = f'Appointment yako imeghairiwa — {appt.reference}'
        body = (f'Habari {appt.full_name},\n\nAppointment yako {appt.reference} ya {day} saa {hour} imeghairiwa.{reason}\n'
                f'Unaweza kupanga appointment mpya kupitia tovuti yetu au WhatsApp {CLC_WHATSAPP}.\n\nCommunity Legal Clinic')
    else:
        subject = f'Your appointment was cancelled — {appt.reference}'
        body = (f'Hello {appt.full_name},\n\nYour appointment {appt.reference} on {day} at {hour} was cancelled.{reason}\n'
                f'You can book a new one on our website or message us on WhatsApp {CLC_WHATSAPP}.\n\nCommunity Legal Clinic')
    return _send(EmailMessage(subject, body, to=[appt.email]))
