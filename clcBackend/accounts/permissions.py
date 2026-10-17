"""Shared API guards for the CLC Admin portal."""
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.authentication import JWTAuthentication

from core.models import ActorType
from tracking.models import ActivityEvent, UserSession

from .models import MemberRole


class HasPortalRole(BasePermission):
    """Signed in, working as ole, with a portal session that is still open."""
    role = None
    message = 'You cannot do this in the role you are working as.'

    def has_permission(self, request, view):
        claims = request.auth
        if not request.user or not request.user.is_authenticated or claims is None:
            return False
        if claims.get('scope') != 'portal' or claims.get('role') != self.role:
            return False
        return UserSession.objects.filter(id=claims.get('session_id'), membership_id=claims.get('membership_id'),
                                          ended_at__isnull=True).exists()


class IsClcAdmin(HasPortalRole):
    role = MemberRole.CLC_ADMIN
    message = 'Only a CLC Admin can do this.'


class IsAdvocate(HasPortalRole):
    role = MemberRole.ADVOCATE
    message = 'Only an advocate can do this.'


def admin_api(methods):
    def wrap(fn):
        return api_view(methods)(authentication_classes([JWTAuthentication])(permission_classes([IsClcAdmin])(fn)))
    return wrap


def advocate_api(methods):
    def wrap(fn):
        return api_view(methods)(authentication_classes([JWTAuthentication])(permission_classes([IsAdvocate])(fn)))
    return wrap


def log_staff_action(request, entity_type, entity_id, action, after=None):
    """Audit entry under the role the admin is working as."""
    ActivityEvent.objects.create(
        actor_type=ActorType.STAFF, session_id=request.auth.get('session_id'),
        acting_membership_id=request.auth.get('membership_id'), entity_type=entity_type, entity_id=entity_id,
        action=action, after_data=after,
    )
