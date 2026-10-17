"""Law firm registration and onboarding: application -> CLC review -> meeting -> agreements
-> approval -> 2-month trial -> active. A registration is an application for approval,
never automatic access."""
import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AppStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    SUBMITTED = 'submitted', 'Submitted'
    UNDER_REVIEW = 'under_review', 'Under review'
    CORRECTION_REQUIRED = 'correction_required', 'Correction required'
    MEETING_PENDING = 'meeting_pending', 'Meeting pending'
    AGREEMENTS_PENDING = 'agreements_pending', 'Agreements pending'
    APPROVED_FOR_TRIAL = 'approved_for_trial', 'Approved for trial'
    ACTIVE = 'active', 'Active'
    SUSPENDED = 'suspended', 'Suspended'
    REJECTED = 'rejected', 'Rejected'


class DocType(models.TextChoices):
    BRELA_CERTIFICATE = 'brela_certificate', 'BRELA Certificate of Registration'
    BRELA_EXTRACT = 'brela_extract', 'Current BRELA Extract from Register'
    PARTNERSHIP_DEED = 'partnership_deed', 'Partnership Deed / Agreement'
    BUSINESS_LICENCE = 'business_licence', 'Current Business Licence'
    TIN_CERTIFICATE = 'tin_certificate', 'TIN Certificate'
    OFFICE_PROOF = 'office_proof', 'Proof of office address'
    OFFICE_PHOTOS = 'office_photos', 'Office photographs'
    REP_ID = 'rep_id', 'Identification of Managing Partner / Authorized Representative'
    PRACTISING_CERTIFICATE = 'practising_certificate', 'Practising Certificate'
    OTHER = 'other', 'Other'


# Documents every firm must provide (practising certificates are required per advocate)
REQUIRED_FIRM_DOCS = [
    DocType.BRELA_CERTIFICATE, DocType.BRELA_EXTRACT, DocType.PARTNERSHIP_DEED, DocType.BUSINESS_LICENCE,
    DocType.TIN_CERTIFICATE, DocType.OFFICE_PROOF, DocType.OFFICE_PHOTOS, DocType.REP_ID,
]
MULTI_FILE_DOCS = {DocType.OFFICE_PHOTOS, DocType.OTHER}  # more than one current file allowed


class DocStatus(models.TextChoices):
    PENDING = 'pending', 'Pending review'
    ACCEPTED = 'accepted', 'Accepted'
    REPLACEMENT_REQUESTED = 'replacement_requested', 'Replacement requested'


class RequestKind(models.TextChoices):
    CORRECTION = 'correction', 'Correction'
    DOCUMENT = 'document', 'Additional / replacement document'


class MeetingType(models.TextChoices):
    PHYSICAL = 'physical', 'Physical'
    ONLINE = 'online', 'Online'


class MeetingOutcome(models.TextChoices):
    SCHEDULED = 'scheduled', 'Scheduled'
    COMPLETED = 'completed', 'Completed'
    NOT_SATISFACTORY = 'not_satisfactory', 'Not satisfactory'
    CANCELLED = 'cancelled', 'Cancelled'


class AgreementType(models.TextChoices):
    ENGAGEMENT = 'engagement', 'CLC-Law Firm Engagement / Participation Agreement'
    CODE_OF_CONDUCT = 'code_of_conduct', 'CLC Code of Conduct'
    NDA = 'nda', 'Non-Disclosure Agreement (NDA)'
    FINANCIAL_TERMS = 'financial_terms', 'Financial / Tax Terms or Schedule'


class PracticeArea(models.Model):
    """Selectable areas of expertise. 'Other - Specify' is free text on the firm or advocate."""
    code = models.TextField(unique=True)
    name_en = models.TextField()
    name_sw = models.TextField()
    sort_order = models.SmallIntegerField(default=0)

    class Meta:
        db_table = 'practice_areas'
        ordering = ['sort_order', 'name_en']

    def __str__(self):
        return self.name_en


class FirmApplication(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField('accounts.Organization', null=True, blank=True, on_delete=models.PROTECT,
                                        related_name='application')  # created when the application is submitted
    application_number = models.TextField(null=True, blank=True, unique=True)
    token_hash = models.CharField(max_length=64)  # applicant's secret link; only the hash is stored
    status = models.CharField(max_length=24, choices=AppStatus.choices, default=AppStatus.DRAFT)
    status_before_suspension = models.CharField(max_length=24, null=True, blank=True)
    status_reason = models.TextField(null=True, blank=True)
    created_by_admin = models.BooleanField(default=False)  # a CLC Admin filled it in: partners and advocates are optional

    firm_name = models.TextField(blank=True, default='')
    brela_number = models.TextField(blank=True, default='')
    registration_date = models.DateField(null=True, blank=True)
    tin = models.TextField(blank=True, default='')
    licence_number = models.TextField(blank=True, default='')
    licence_expiry = models.DateField(null=True, blank=True)

    region = models.TextField(blank=True, default='')
    district = models.TextField(blank=True, default='')
    ward = models.TextField(blank=True, default='')
    street = models.TextField(blank=True, default='')
    postal_address = models.TextField(blank=True, default='')
    map_location = models.TextField(blank=True, default='')

    phone_e164 = models.TextField(blank=True, default='')
    whatsapp_e164 = models.TextField(blank=True, default='')
    email = models.TextField(blank=True, default='')
    website = models.TextField(blank=True, default='')
    contact_person = models.TextField(blank=True, default='')
    contact_person_phone = models.TextField(blank=True, default='')

    # Firm expertise is stored separately from each advocate's expertise
    practice_areas = models.ManyToManyField(PracticeArea, through='FirmExpertise', related_name='firms')
    other_areas = models.TextField(blank=True, default='')
    regions_served = ArrayField(models.TextField(), default=list, blank=True)
    languages = ArrayField(models.CharField(max_length=2), default=list, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    review_started_at = models.DateTimeField(null=True, blank=True)
    reviewer = models.ForeignKey('accounts.Membership', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)

    # Stored separately: a firm can be billed from a date other than the end of its trial
    trial_start = models.DateField(null=True, blank=True)
    trial_end = models.DateField(null=True, blank=True)
    billing_effective = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'firm_applications'
        indexes = [models.Index(fields=['status', 'submitted_at'], name='firm_apps_by_status')]

    def __str__(self):
        return self.application_number or f'Draft {self.firm_name}'


class FirmExpertise(models.Model):
    application = models.ForeignKey(FirmApplication, on_delete=models.CASCADE, related_name='expertise')
    area = models.ForeignKey(PracticeArea, on_delete=models.PROTECT)
    is_primary = models.BooleanField(default=False)  # up to 5 per firm

    class Meta:
        db_table = 'firm_expertise'
        constraints = [models.UniqueConstraint(fields=['application', 'area'], name='firm_expertise_uq')]


class FirmPartner(models.Model):
    """Every partner / proprietor; exactly one is the authorized representative."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(FirmApplication, on_delete=models.CASCADE, related_name='partners')
    full_name = models.TextField()
    id_number = models.TextField(blank=True, default='')
    email = models.TextField(blank=True, default='')
    phone_e164 = models.TextField(blank=True, default='')
    is_managing_partner = models.BooleanField(default=False)
    is_authorized_rep = models.BooleanField(default=False)

    class Meta:
        db_table = 'firm_partners'
        constraints = [
            models.UniqueConstraint(fields=['application'], condition=Q(is_authorized_rep=True),
                                    name='firm_partners_one_rep'),
        ]


class ApplicationAdvocate(models.Model):
    """An advocate who will handle CLC cases. Becomes a user + advocate profile on approval."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(FirmApplication, on_delete=models.CASCADE, related_name='advocates')
    full_name = models.TextField()
    roll_number = models.TextField()
    phone_e164 = models.TextField(blank=True, default='')
    email = models.TextField()
    practising_certificate = models.TextField(blank=True, default='')
    certificate_expiry = models.DateField(null=True, blank=True)
    practice_areas = models.ManyToManyField(PracticeArea, related_name='advocates')
    other_areas = models.TextField(blank=True, default='')
    user = models.OneToOneField('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='application_advocate')

    class Meta:
        db_table = 'application_advocates'
        constraints = [models.UniqueConstraint(fields=['application', 'roll_number'], name='app_advocates_roll_uq')]


def upload_path(instance, filename):
    return f'onboarding/{instance.application_id}/{uuid.uuid4().hex}_{filename}'


class ApplicationDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(FirmApplication, on_delete=models.CASCADE, related_name='documents')
    advocate = models.ForeignKey(ApplicationAdvocate, null=True, blank=True, on_delete=models.CASCADE,
                                 related_name='documents')  # practising certificates
    doc_type = models.CharField(max_length=32, choices=DocType.choices)
    file = models.FileField(upload_to=upload_path, max_length=300)
    file_name = models.TextField()
    mime_type = models.TextField()
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=24, choices=DocStatus.choices, default=DocStatus.PENDING)
    reviewer_note = models.TextField(blank=True, default='')
    reviewed_by = models.ForeignKey('accounts.Membership', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    is_current = models.BooleanField(default=True)  # false once replaced by a newer upload
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'application_documents'
        ordering = ['uploaded_at']


class ReviewRequest(models.Model):
    """CLC asking for a correction, or for a replacement / additional document."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(FirmApplication, on_delete=models.CASCADE, related_name='requests')
    kind = models.CharField(max_length=16, choices=RequestKind.choices)
    doc_type = models.CharField(max_length=32, choices=DocType.choices, null=True, blank=True)
    advocate = models.ForeignKey(ApplicationAdvocate, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    message = models.TextField()
    created_by = models.ForeignKey('accounts.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'application_requests'
        ordering = ['created_at']


class OnboardingMeeting(models.Model):
    """The pre-approval meeting between CLC and the firm."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(FirmApplication, on_delete=models.CASCADE, related_name='meetings')
    scheduled_at = models.DateTimeField()
    meeting_type = models.CharField(max_length=16, choices=MeetingType.choices)
    location_or_link = models.TextField(blank=True, default='')
    participants = models.TextField(blank=True, default='')
    topics = models.JSONField(default=list, blank=True)  # platform use, confidentiality, conflicts, financial/tax, trial...
    notes = models.TextField(blank=True, default='')
    outcome = models.CharField(max_length=24, choices=MeetingOutcome.choices, default=MeetingOutcome.SCHEDULED)
    recorded_by = models.ForeignKey('accounts.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'onboarding_meetings'
        ordering = ['scheduled_at']


class Agreement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(FirmApplication, on_delete=models.CASCADE, related_name='agreements')
    agreement_type = models.CharField(max_length=24, choices=AgreementType.choices)
    signed_at = models.DateField(null=True, blank=True)
    signed_by_name = models.TextField(blank=True, default='')  # no longer asked for; kept for old rows
    file = models.FileField(upload_to=upload_path, max_length=300, null=True, blank=True)  # the signed agreement
    file_name = models.TextField(blank=True, default='')
    recorded_by = models.ForeignKey('accounts.Membership', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        db_table = 'application_agreements'
        constraints = [models.UniqueConstraint(fields=['application', 'agreement_type'], name='agreements_uq')]
