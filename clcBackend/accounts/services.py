import os
import secrets
import string

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from onboarding.models import PracticeArea

from .models import AdvocateProfile, MemberRole, Membership, MembershipStatus, Organization, OrgStatus, OrgType, User

CLC_NAME = 'Community Legal Clinic'


def get_clc_org():
    """The single CLC organization row, created on first use."""
    org = Organization.objects.filter(type=OrgType.CLC).first()
    if org is None:
        org = Organization.objects.create(
            type=OrgType.CLC, name=CLC_NAME, status=OrgStatus.APPROVED, approved_at=timezone.now(),
        )
    return org


def ensure_admin_membership(user):
    """Every superuser is a CLC administrator: give them an active clc_admin role."""
    membership, created = Membership.objects.get_or_create(
        user=user, organization=get_clc_org(), role=MemberRole.CLC_ADMIN,
        defaults={'status': MembershipStatus.ACTIVE},
    )
    if not created and membership.status != MembershipStatus.ACTIVE:
        membership.status = MembershipStatus.ACTIVE
        membership.save(update_fields=['status', 'updated_at'])
    return membership


def temp_password():
    """Readable 12-character temporary password (no look-alike characters)."""
    alphabet = string.ascii_letters.replace('l', '').replace('I', '').replace('O', '') + string.digits.replace('0', '')
    return ''.join(secrets.choice(alphabet) for _ in range(12))


CERTIFICATE_TYPES = {'.pdf', '.jpg', '.jpeg', '.png'}
MAX_CERTIFICATE_BYTES = 10 * 1024 * 1024


class UserCreateError(Exception):
    pass


def create_user_with_role(admin_membership, full_name, mail, phone, role, organization=None, advocate=None):
    """Create a user (or reuse the existing one by email) with one active role, and email a
    temporary password. `advocate` = {roll_number, practising_certificate, certificate_expires_on, practice_areas (codes),
    other_areas, certificate_file}.
    Returns the membership."""
    mail = (mail or '').strip().lower()
    if role not in MemberRole.values:
        raise UserCreateError('Choose a role.')
    if role == MemberRole.CLC_ADMIN:
        organization = get_clc_org()
    elif organization is None or organization.type != OrgType.LAW_FIRM or organization.status != OrgStatus.APPROVED:
        raise UserCreateError('Choose a registered (approved) law firm.')
    if role == MemberRole.ADVOCATE:
        advocate = advocate or {}
        if not (advocate.get('roll_number') and advocate.get('practising_certificate') and advocate.get('certificate_expires_on')):
            raise UserCreateError('An advocate needs a roll number, practising certificate number and its expiry date.')
        upload = advocate.get('certificate_file')
        if upload is None:
            raise UserCreateError('Upload the advocate\'s practising certificate.')
        if os.path.splitext(upload.name)[1].lower() not in CERTIFICATE_TYPES:
            raise UserCreateError('Upload the certificate as a PDF, JPG or PNG file.')
        if not 0 < upload.size <= MAX_CERTIFICATE_BYTES:
            raise UserCreateError('The certificate file must not be empty and can be at most 10 MB.')
        areas = list(PracticeArea.objects.filter(code__in=advocate.get('practice_areas') or []))
        if not areas and not (advocate.get('other_areas') or '').strip():
            raise UserCreateError('Choose at least one area of expertise.')
        clash = AdvocateProfile.objects.filter(roll_number__iexact=advocate['roll_number'])
        existing = User.objects.filter(email__iexact=mail).first()
        if existing:
            clash = clash.exclude(user=existing)
        if clash.exists():
            raise UserCreateError('That roll number already belongs to another advocate.')

    user = User.objects.filter(email__iexact=mail).first()
    temp = None
    if user is None:
        temp = temp_password()
        user = User(email=mail, full_name=full_name.strip(), phone_e164=phone or None, must_change_password=True)
        user.set_password(temp)
        user.save()
    if Membership.objects.filter(user=user, organization=organization, role=role).exists():
        raise UserCreateError('This person already has that role.')
    membership = Membership.objects.create(user=user, organization=organization, role=role,
                                           status=MembershipStatus.ACTIVE, invited_by=admin_membership)
    if role == MemberRole.ADVOCATE:
        profile, _ = AdvocateProfile.objects.update_or_create(user=user, defaults={
            'roll_number': advocate['roll_number'], 'practising_certificate': advocate['practising_certificate'],
            'certificate_expires_on': advocate['certificate_expires_on'], 'verified_at': timezone.now(),
            'verified_by': admin_membership, 'other_areas': (advocate.get('other_areas') or '').strip()})
        profile.certificate_file.save(advocate['certificate_file'].name[:100], advocate['certificate_file'], save=True)
        profile.practice_areas.set(areas)

    role_name = membership.get_role_display()
    body = (f'Hello {user.full_name},\n\nAn account was created for you on the CLC platform as {role_name} '
            f'({organization.name}).\n\n')
    if temp:
        body += (f'Username: {mail}\nTemporary password: {temp}\n'
                 f'You will be asked to choose a new password the first time you sign in.\n\n')
    body += f'Sign in: {settings.FRONTEND_URL}/login'
    try:
        send_mail('Your CLC platform account', body, settings.DEFAULT_FROM_EMAIL, [mail])
    except Exception:  # a mail outage must not undo the account
        pass
    return membership