"""Numbers for the CLC Admin overview screen."""
from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.response import Response

from accounts.models import Organization, OrgType, User
from accounts.permissions import admin_api
from cases.models import Case, CaseStatus
from conversations.models import BotFlow, Conversation, FlowStatus
from onboarding.models import AppStatus, FirmApplication

AWAITING = [AppStatus.SUBMITTED, AppStatus.UNDER_REVIEW, AppStatus.CORRECTION_REQUIRED,
            AppStatus.MEETING_PENDING, AppStatus.AGREEMENTS_PENDING]


@admin_api(['GET'])
def admin_overview(request):
    today = timezone.localdate()
    week_start = today - timedelta(days=6)

    firms = Organization.objects.filter(type=OrgType.LAW_FIRM)
    awaiting = FirmApplication.objects.filter(status__in=AWAITING).order_by('submitted_at')

    flows = BotFlow.objects.annotate(
        live=Count('versions', filter=Q(versions__status=FlowStatus.PUBLISHED)),
        draft=Count('versions', filter=Q(versions__status=FlowStatus.DRAFT)),
    )

    open_cases = Case.objects.exclude(status=CaseStatus.CLOSED)

    per_day = dict(
        Conversation.objects.filter(channel='whatsapp', opened_at__date__gte=week_start)
        .annotate(day=TruncDate('opened_at')).values_list('day').annotate(n=Count('id')).order_by('day'))
    days = [week_start + timedelta(days=i) for i in range(7)]

    return Response({
        'law_firms': {'total': firms.count(), 'approved': firms.filter(status='approved').count(),
                      'awaiting_approval': awaiting.count()},
        'users': {'total': User.objects.count(), 'not_signed_in_yet': User.objects.filter(last_login__isnull=True).count()},
        'flows': {'total': flows.count(), 'live': sum(1 for f in flows if f.live and f.is_active),
                  'with_changes': sum(1 for f in flows if f.draft)},
        'cases': {'open': open_cases.count(), 'new_this_week': Case.objects.filter(created_at__date__gte=week_start).count()},
        'conversations_per_day': [{'d': d.strftime('%a'), 'date': d, 'v': per_day.get(d, 0)} for d in days],
        'awaiting_firms': [{'id': str(a.id), 'firm_name': a.firm_name, 'region': a.region,
                            'application_number': a.application_number, 'status': a.status,
                            'status_label': a.get_status_display()} for a in awaiting[:8]],
    })
