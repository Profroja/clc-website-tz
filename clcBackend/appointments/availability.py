"""Which times can be booked: CLC's office hours, minus public holidays, minus times already taken."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from core.models import OfficeHours, PublicHoliday

from .models import Appointment, AppointmentStatus

TZ = ZoneInfo('Africa/Dar_es_Salaam')  # East Africa Time: what visitors and staff see
SLOT_MINUTES = 30
LEAD_HOURS = 2  # a visitor cannot book a time that starts within the next two hours
HORIZON_DAYS = 30


def now_local():
    return timezone.now().astimezone(TZ)


def hours_for(day):
    """Office hours for a date, or None when CLC is closed (not open that weekday, or a public holiday)."""
    if PublicHoliday.objects.filter(holiday_date=day).exists():
        return None
    sunday_based = (day.weekday() + 1) % 7  # the OfficeHours table counts Sunday as 0
    return OfficeHours.objects.filter(weekday=sunday_based).first()


def slot_times(day):
    """Every bookable start time of a day (ignoring what is already taken)."""
    hours = hours_for(day)
    if not hours:
        return []
    start = datetime.combine(day, hours.opens_at, tzinfo=TZ)
    end = datetime.combine(day, hours.closes_at, tzinfo=TZ)
    slots = []
    while start + timedelta(minutes=SLOT_MINUTES) <= end:
        slots.append(start)
        start += timedelta(minutes=SLOT_MINUTES)
    return slots


def taken(day):
    first = datetime.combine(day, time.min, tzinfo=TZ)
    rows = Appointment.objects.filter(status=AppointmentStatus.SCHEDULED, scheduled_at__gte=first,
                                      scheduled_at__lt=first + timedelta(days=1)).values_list('scheduled_at', flat=True)
    return {r.astimezone(TZ) for r in rows}


def slots_for(day):
    """[{time: '09:00', available: bool}] for one date."""
    earliest = now_local() + timedelta(hours=LEAD_HOURS)
    booked = taken(day)
    return [{'time': s.strftime('%H:%M'), 'available': s >= earliest and s not in booked} for s in slot_times(day)]


def days_ahead():
    """The next HORIZON_DAYS dates with how many free slots each has (0 = closed or full)."""
    today = now_local().date()
    out = []
    for i in range(HORIZON_DAYS + 1):
        day = today + timedelta(days=i)
        free = sum(1 for s in slots_for(day) if s['available'])
        out.append({'date': day.isoformat(), 'open': hours_for(day) is not None, 'free': free})
    return out


def is_bookable(moment):
    """True if `moment` (aware) is a valid, free slot start within the booking window."""
    local = moment.astimezone(TZ)
    day = local.date()
    if day > now_local().date() + timedelta(days=HORIZON_DAYS):
        return False
    return any(s['time'] == local.strftime('%H:%M') and s['available'] for s in slots_for(day)) and local.second == 0
