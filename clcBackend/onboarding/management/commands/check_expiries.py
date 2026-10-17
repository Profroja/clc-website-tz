from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import MemberRole, Membership, MembershipStatus
from notifications.models import Notification, NotifyChannel
from onboarding import services


class Command(BaseCommand):
    help = ('Alert CLC Admins before business licences and practising certificates expire. '
            'Run daily (cron / scheduled task). Each item is alerted at most once a week.')

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=services.EXPIRY_WARNING_DAYS)

    def handle(self, *args, **opts):
        admins = list(Membership.objects.filter(role=MemberRole.CLC_ADMIN, status=MembershipStatus.ACTIVE))
        recent = timezone.now() - timedelta(days=7)
        sent = 0
        for item in services.expiring_items(opts['days']):
            what = 'Business licence' if item['kind'] == 'business_licence' else 'Practising certificate'
            state = 'has EXPIRED' if item['expired'] else f"expires in {item['days_left']} days"
            subject = f"{what} of {item['name']} {state}"
            for m in admins:
                if Notification.objects.filter(membership=m, entity_id=item['application_id'], subject=subject,
                                               created_at__gte=recent).exists():
                    continue
                Notification.objects.create(
                    membership=m, channel=NotifyChannel.IN_APP, subject=subject, entity_type='firm_application',
                    entity_id=item['application_id'], body=f"{item['firm']} - expiry date {item['expires_on']}")
                sent += 1
        self.stdout.write(f'{sent} alert(s) created.')
