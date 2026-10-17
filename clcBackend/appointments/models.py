import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import Lang


class MeetingKind(models.TextChoices):
    WHATSAPP = 'whatsapp', 'WhatsApp call'
    PHONE = 'phone', 'Phone call'
    GOOGLE_MEET = 'google_meet', 'Google Meet'
    IN_PERSON = 'in_person', 'In person'


class AppointmentStatus(models.TextChoices):
    SCHEDULED = 'scheduled', 'Scheduled'
    COMPLETED = 'completed', 'Completed'
    CANCELLED = 'cancelled', 'Cancelled'
    NO_SHOW = 'no_show', 'No show'


class Appointment(models.Model):
    """A consultation booked by a visitor on the website."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.TextField(unique=True)  # APT/2026/0007
    service = models.TextField()  # what the visitor needs help with (a package title, or general advice)
    meeting_kind = models.CharField(max_length=16, choices=MeetingKind.choices)
    scheduled_at = models.DateTimeField()
    duration_minutes = models.SmallIntegerField(default=30)
    full_name = models.TextField()
    phone_e164 = models.TextField()
    email = models.TextField()
    language = models.CharField(max_length=2, choices=Lang.choices, default=Lang.SW)
    note = models.TextField(blank=True, default='')
    status = models.CharField(max_length=16, choices=AppointmentStatus.choices, default=AppointmentStatus.SCHEDULED)
    status_note = models.TextField(blank=True, default='')  # e.g. why it was cancelled
    advocate = models.ForeignKey('accounts.Membership', null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='appointments')  # the advocate who takes this consultation
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'appointments'
        ordering = ['scheduled_at']
        constraints = [
            # One consultation per time slot: two visitors can never book the same time.
            models.UniqueConstraint(fields=['scheduled_at'], condition=Q(status='scheduled'), name='appointments_one_per_slot'),
        ]

    def __str__(self):
        return f'{self.reference} {self.full_name}'
