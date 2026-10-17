from datetime import timedelta

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from tracking.models import SessionEndReason, UserSession

from .models import MembershipStatus, OrgStatus, OrgType
from .services import ensure_admin_membership


def usable_memberships(user):
    """Roles shown in the 'Working as' chooser: active roles in CLC or an approved firm."""
    qs = user.memberships.filter(status=MembershipStatus.ACTIVE).select_related('organization')
    return [
        m for m in qs
        if m.organization.type == OrgType.CLC or m.organization.status == OrgStatus.APPROVED
    ]


def membership_payload(m):
    return {
        'id': str(m.id),
        'role': m.role,
        'role_label': m.get_role_display(),
        'organization': m.organization.name,
        'label': f'{m.organization.name} - {m.get_role_display()}',
    }


def issue_refresh_token(user, membership, session):
    """Refresh token whose claims (copied onto every access token made from it) say who is working as what."""
    token = RefreshToken.for_user(user)
    token['scope'] = 'portal'
    token['membership_id'] = str(membership.id)
    token['role'] = membership.role
    token['organization_id'] = str(membership.organization_id)
    token['session_id'] = str(session.id)
    return token


def client_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR')


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def login(request):
    """Step 1 for every user: check the password, return the roles they can work as."""
    email = (request.data.get('email') or '').strip()
    password = request.data.get('password') or ''
    user = authenticate(request, username=email, password=password)
    if user is None:
        return Response({'detail': 'invalid_credentials'}, status=status.HTTP_401_UNAUTHORIZED)

    if user.is_superuser:
        ensure_admin_membership(user)  # covers superusers created before this rule existed
    memberships = usable_memberships(user)
    if not memberships:
        return Response({'detail': 'no_active_role'}, status=status.HTTP_403_FORBIDDEN)

    # Short-lived token that can only be used to pick a role.
    token = RefreshToken.for_user(user).access_token
    token['scope'] = 'pick_role'
    token.set_exp(lifetime=timedelta(minutes=10))
    return Response({
        'pick_role_token': str(token),
        'user': {'id': str(user.id), 'full_name': user.full_name, 'email': user.email},
        'must_change_password': user.must_change_password,  # temporary password: the user must choose a new one first
        'memberships': [membership_payload(m) for m in memberships],
    })


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def change_password(request):
    """First login with a temporary password (also usable later). Needs the password-step token."""
    if request.auth.get('scope') not in ('pick_role', 'portal'):
        return Response({'detail': 'invalid_token'}, status=status.HTTP_401_UNAUTHORIZED)
    user = request.user
    if not user.check_password(request.data.get('current_password') or ''):
        return Response({'detail': 'invalid_credentials'}, status=status.HTTP_400_BAD_REQUEST)
    new = request.data.get('new_password') or ''
    try:
        validate_password(new, user)
    except ValidationError as exc:
        return Response({'detail': 'weak_password', 'errors': list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
    if new == request.data.get('current_password'):
        return Response({'detail': 'weak_password', 'errors': ['Choose a password different from the temporary one.']},
                        status=status.HTTP_400_BAD_REQUEST)
    user.set_password(new)
    user.must_change_password = False
    user.save()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def select_role(request):
    """Step 2: 'Working as'. Starts a portal session and issues the real tokens."""
    if request.auth.get('scope') not in ('pick_role', 'portal'):
        return Response({'detail': 'invalid_token'}, status=status.HTTP_401_UNAUTHORIZED)
    user = request.user
    if user.must_change_password:
        return Response({'detail': 'password_change_required'}, status=status.HTTP_403_FORBIDDEN)
    membership = next((m for m in usable_memberships(user) if str(m.id) == str(request.data.get('membership_id'))), None)
    if membership is None:
        return Response({'detail': 'invalid_role'}, status=status.HTTP_403_FORBIDDEN)

    # Switching role ends the previous session.
    if request.auth.get('scope') == 'portal':
        UserSession.objects.filter(id=request.auth.get('session_id'), ended_at__isnull=True).update(
            ended_at=timezone.now(), end_reason=SessionEndReason.SWITCHED_ROLE)

    session = UserSession.objects.create(
        user=user, membership=membership, ip_address=client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
    )
    user.last_login = timezone.now()
    user.save(update_fields=['last_login'])

    refresh_token = issue_refresh_token(user, membership, session)
    return Response({
        'access': str(refresh_token.access_token),
        'refresh': str(refresh_token),
        'membership': membership_payload(membership),
        'user': {'id': str(user.id), 'full_name': user.full_name, 'email': user.email},
    })


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def refresh(request):
    """Trade a refresh token for a new access token. The refresh token is rotated: the old one
    is blacklisted and a new one returned. Fails once the portal session has ended or the role
    is no longer usable, so signing out or suspending someone takes effect within minutes."""
    try:
        old = RefreshToken(request.data.get('refresh') or '')
    except TokenError:
        return Response({'detail': 'invalid_token'}, status=status.HTTP_401_UNAUTHORIZED)
    if old.get('scope') != 'portal':
        return Response({'detail': 'invalid_token'}, status=status.HTTP_401_UNAUTHORIZED)

    session = UserSession.objects.select_related('user', 'membership__organization').filter(
        id=old.get('session_id'), ended_at__isnull=True).first()
    usable = session and session.user.is_active and any(
        m.id == session.membership_id for m in usable_memberships(session.user))
    if not usable:
        return Response({'detail': 'session_ended'}, status=status.HTTP_401_UNAUTHORIZED)

    old.blacklist()
    new = issue_refresh_token(session.user, session.membership, session)
    return Response({'access': str(new.access_token), 'refresh': str(new)})


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def logout(request):
    raw = request.data.get('refresh')
    if raw:  # a signed-out refresh token can never be used again
        try:
            RefreshToken(raw).blacklist()
        except TokenError:
            pass
    UserSession.objects.filter(id=request.auth.get('session_id'), ended_at__isnull=True).update(
        ended_at=timezone.now(), end_reason=SessionEndReason.SIGN_OUT)
    return Response(status=status.HTTP_204_NO_CONTENT)
