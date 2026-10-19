from django.core.mail import send_mail
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework.response import Response

from tracking.models import ActivityEvent

from .models import Membership, MemberRole, Organization, OrgStatus, OrgType
from .permissions import admin_api
from .models import User
from .services import UserCreateError, create_user_with_role, temp_password


def membership_row(m):
    return {
        'id': str(m.id), 'user_id': str(m.user_id), 'full_name': m.user.full_name, 'email': m.user.email,
        'phone': m.user.phone_e164 or '', 'role': m.role, 'role_label': m.get_role_display(),
        'organization': m.organization.name, 'status': m.status if m.user.is_active else 'inactive',
        'signed_in': m.user.last_login is not None, 'is_active': m.user.is_active,
    }


@admin_api(['GET', 'POST'])
def users(request):
    if request.method == 'GET':
        rows = (Membership.objects.select_related('user', 'organization').order_by('organization__name', 'user__full_name'))
        return Response([membership_row(m) for m in rows])

    d = request.data
    full_name, email = (d.get('full_name') or '').strip(), (d.get('email') or '').strip()
    if not full_name or '@' not in email:
        return Response({'detail': 'Enter the full name and a valid email.'}, status=400)
    organization = None
    if d.get('organization_id'):
        organization = Organization.objects.filter(id=d['organization_id']).first()
    expiry = parse_date(str(d.get('certificate_expires_on') or ''))
    try:
        membership = create_user_with_role(
            Membership.objects.get(id=request.auth.get('membership_id')), full_name, email, (d.get('phone') or '').strip(),
            d.get('role'), organization,
            {'roll_number': (d.get('roll_number') or '').strip(), 'practising_certificate': (d.get('practising_certificate') or '').strip(),
             'certificate_expires_on': expiry, 'certificate_file': request.FILES.get('certificate'),
             'practice_areas': d.getlist('practice_areas') if hasattr(d, 'getlist') else (d.get('practice_areas') or []),
             'other_areas': d.get('other_areas') or ''})
    except UserCreateError as exc:
        return Response({'detail': str(exc)}, status=400)
    ActivityEvent.objects.create(
        actor_type='staff', session_id=request.auth.get('session_id'), acting_membership_id=request.auth.get('membership_id'),
        entity_type='user', entity_id=membership.user_id, action='user.created', after_data={'role': membership.role, 'organization': organization.name if organization else 'CLC'})
    return Response(membership_row(membership), status=201)


@admin_api(['GET'])
def registered_firms(request):
    """Approved law firms that a firm admin or advocate can be attached to."""
    firms = Organization.objects.filter(type=OrgType.LAW_FIRM, status=OrgStatus.APPROVED).order_by('name')
    return Response([{'id': str(f.id), 'name': f.name} for f in firms])


def _audit(request, user_id, action, after=None):
    ActivityEvent.objects.create(
        actor_type='staff', session_id=request.auth.get('session_id'), acting_membership_id=request.auth.get('membership_id'),
        entity_type='user', entity_id=user_id, action=action, after_data=after)


@admin_api(['PATCH'])
def edit_user(request, membership_id):
    """Change a person's name, email or phone (applies to all their roles)."""
    m = get_object_or_404(Membership.objects.select_related('user', 'organization'), id=membership_id)
    user, d = m.user, request.data
    full_name = (d.get('full_name', user.full_name) or '').strip()
    email = (d.get('email', user.email) or '').strip().lower()
    if not full_name or '@' not in email:
        return Response({'detail': 'Enter the full name and a valid email.'}, status=400)
    if User.objects.filter(email__iexact=email).exclude(id=user.id).exists():
        return Response({'detail': 'Another account already uses that email.'}, status=400)
    user.full_name, user.email = full_name, email
    user.phone_e164 = (d.get('phone', user.phone_e164 or '') or '').strip() or None
    user.save()
    _audit(request, user.id, 'user.updated', {'full_name': full_name, 'email': email})
    return Response(membership_row(m))


def _set_active(request, membership_id, active):
    m = get_object_or_404(Membership.objects.select_related('user', 'organization'), id=membership_id)
    if not active and str(m.user_id) == str(request.user.id):
        return Response({'detail': 'You cannot deactivate your own account.'}, status=400)
    m.user.is_active = active
    m.user.save(update_fields=['is_active', 'updated_at'])
    _audit(request, m.user_id, 'user.activated' if active else 'user.deactivated')
    return Response(membership_row(m))


@admin_api(['POST'])
def deactivate_user(request, membership_id):
    return _set_active(request, membership_id, False)


@admin_api(['POST'])
def activate_user(request, membership_id):
    return _set_active(request, membership_id, True)


@admin_api(['POST'])
def reset_password(request, membership_id):
    """Give the person a new temporary password (emailed, and returned once so the admin can pass it on)."""
    m = get_object_or_404(Membership.objects.select_related('user'), id=membership_id)
    temp = temp_password()
    m.user.set_password(temp)
    m.user.must_change_password = True
    m.user.save()
    try:
        send_mail('Your CLC password was reset',
                  f'Hello {m.user.full_name},\n\nA CLC administrator set a new temporary password for you.\n\n'
                  f'Username: {m.user.email}\nTemporary password: {temp}\n'
                  f'You will be asked to choose a new password when you sign in: {settings.FRONTEND_URL}/login',
                  settings.DEFAULT_FROM_EMAIL, [m.user.email])
    except Exception:
        pass
    _audit(request, m.user_id, 'user.password_reset')
    return Response({'temporary_password': temp, 'email': m.user.email})