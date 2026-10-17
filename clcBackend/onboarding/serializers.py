"""Input validation for the public application form. Drafts may be incomplete; completeness
is checked when the firm submits (services.submission_problems)."""
import re

from rest_framework import serializers

from .models import PracticeArea

LANGS = ('sw', 'en', 'zh')


def normalize_phone(value):
    """+255712345678 style; a local 07… number is taken as Tanzanian."""
    raw = re.sub(r'[\s\-().]', '', value or '')
    if not raw:
        return ''
    if raw.startswith('00'):
        raw = '+' + raw[2:]
    elif raw.startswith('0'):
        raw = '+255' + raw[1:]
    elif not raw.startswith('+'):
        raw = '+' + raw
    if not re.fullmatch(r'\+\d{9,15}', raw):
        raise serializers.ValidationError('Enter a valid phone number, e.g. +255 712 345 678.')
    return raw


class Phone(serializers.CharField):
    def __init__(self, **kw):
        super().__init__(required=False, allow_blank=True, **kw)

    def to_internal_value(self, data):
        return normalize_phone(super().to_internal_value(data))


def text(**kw):
    return serializers.CharField(required=False, allow_blank=True, max_length=kw.pop('max_length', 300), **kw)


def optional_date():
    return serializers.DateField(required=False, allow_null=True)


def optional_email():
    return serializers.EmailField(required=False, allow_blank=True, max_length=254)


class PartnerSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    full_name = serializers.CharField(max_length=200)
    id_number = text(max_length=60)
    email = optional_email()
    phone_e164 = Phone()
    is_managing_partner = serializers.BooleanField(required=False, default=False)
    is_authorized_rep = serializers.BooleanField(required=False, default=False)


class AdvocateSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    full_name = serializers.CharField(max_length=200)
    roll_number = serializers.CharField(max_length=60)
    phone_e164 = Phone()
    email = serializers.EmailField(max_length=254)
    practising_certificate = text(max_length=100)
    certificate_expiry = optional_date()
    practice_areas = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    other_areas = text(max_length=300)


class AreaSerializer(serializers.Serializer):
    code = serializers.CharField()
    is_primary = serializers.BooleanField(required=False, default=False)


class ApplicationSerializer(serializers.Serializer):
    firm_name = text(max_length=200)
    brela_number = text(max_length=60)
    registration_date = optional_date()
    tin = text(max_length=40)
    licence_number = text(max_length=60)
    licence_expiry = optional_date()

    region = text(max_length=80)
    district = text(max_length=80)
    ward = text(max_length=80)
    street = text(max_length=200)
    postal_address = text(max_length=200)
    map_location = text(max_length=300)

    phone_e164 = Phone()
    whatsapp_e164 = Phone()
    email = optional_email()
    website = text(max_length=200)
    contact_person = text(max_length=200)
    contact_person_phone = Phone()

    practice_areas = AreaSerializer(many=True, required=False)
    other_areas = text(max_length=300)
    regions_served = serializers.ListField(child=serializers.CharField(max_length=80), required=False, max_length=40)
    languages = serializers.ListField(child=serializers.ChoiceField(choices=LANGS), required=False)

    partners = PartnerSerializer(many=True, required=False, max_length=30)
    advocates = AdvocateSerializer(many=True, required=False, max_length=100)

    def validate_practice_areas(self, value):
        codes = [a['code'] for a in value]
        if len(set(codes)) != len(codes):
            raise serializers.ValidationError('An area was chosen twice.')
        known = set(PracticeArea.objects.filter(code__in=codes).values_list('code', flat=True))
        if set(codes) - known:
            raise serializers.ValidationError(f'Unknown area: {", ".join(sorted(set(codes) - known))}')
        if sum(1 for a in value if a['is_primary']) > 5:
            raise serializers.ValidationError('Choose at most 5 primary areas of expertise.')
        return value

    def validate_partners(self, value):
        if sum(1 for p in value if p['is_authorized_rep']) > 1:
            raise serializers.ValidationError('Only one partner can be the authorized representative.')
        return value

    def validate_advocates(self, value):
        rolls = [a['roll_number'].strip().lower() for a in value]
        emails = [a['email'].strip().lower() for a in value]
        if len(set(rolls)) != len(rolls):
            raise serializers.ValidationError('Two advocates have the same roll number.')
        if len(set(emails)) != len(emails):
            raise serializers.ValidationError('Two advocates have the same email.')
        known = set(PracticeArea.objects.values_list('code', flat=True))
        for a in value:
            if set(a['practice_areas']) - known:
                raise serializers.ValidationError('An advocate has an unknown area of expertise.')
        return value
