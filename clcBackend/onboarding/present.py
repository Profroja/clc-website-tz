"""JSON shapes returned by the onboarding API."""
from tracking.models import ActivityEvent

from . import services
from .models import AgreementType, DocType, PracticeArea, REQUIRED_FIRM_DOCS


def _partner(p):
    return {'id': str(p.id), 'full_name': p.full_name, 'id_number': p.id_number, 'email': p.email,
            'phone_e164': p.phone_e164, 'is_managing_partner': p.is_managing_partner,
            'is_authorized_rep': p.is_authorized_rep}


def _advocate(a):
    return {'id': str(a.id), 'full_name': a.full_name, 'roll_number': a.roll_number, 'phone_e164': a.phone_e164,
            'email': a.email, 'practising_certificate': a.practising_certificate,
            'certificate_expiry': a.certificate_expiry, 'practice_areas': [x.code for x in a.practice_areas.all()],
            'other_areas': a.other_areas, 'has_login': a.user_id is not None}


def _document(d):
    return {'id': str(d.id), 'doc_type': d.doc_type, 'type_label': DocType(d.doc_type).label,
            'advocate_id': str(d.advocate_id) if d.advocate_id else None,
            'advocate_name': d.advocate.full_name if d.advocate_id else None, 'file_name': d.file_name,
            'size_bytes': d.size_bytes, 'status': d.status, 'reviewer_note': d.reviewer_note,
            'uploaded_at': d.uploaded_at}


def _request(r):
    return {'id': str(r.id), 'kind': r.kind, 'doc_type': r.doc_type, 'message': r.message,
            'created_at': r.created_at, 'resolved_at': r.resolved_at}


def _meeting(m, admin):
    data = {'id': str(m.id), 'scheduled_at': m.scheduled_at, 'meeting_type': m.meeting_type,
            'location_or_link': m.location_or_link, 'outcome': m.outcome}
    if admin:
        data.update(participants=m.participants, topics=m.topics, notes=m.notes)
    return data


def _agreement(a):
    return {'type': a.agreement_type, 'label': AgreementType(a.agreement_type).label, 'signed_at': a.signed_at,
            'has_file': bool(a.file), 'file_name': a.file_name}


def application_json(app, admin=False):
    docs = services.current_docs(app)
    data = {
        'id': str(app.id), 'application_number': app.application_number, 'status': app.status,
        'status_label': app.get_status_display(), 'status_reason': app.status_reason,
        'submitted_at': app.submitted_at, 'updated_at': app.updated_at, 'created_at': app.created_at,
        'editable': app.status in services.EDITABLE,
        **{f: getattr(app, f) for f in services.SCALAR_FIELDS},
        'practice_areas': [{'code': e.area.code, 'is_primary': e.is_primary} for e in app.expertise.select_related('area')],
        'partners': [_partner(p) for p in app.partners.all()],
        'advocates': [_advocate(a) for a in app.advocates.prefetch_related('practice_areas')],
        'documents': [_document(d) for d in docs],
        'requests': [_request(r) for r in app.requests.all()],
        'meetings': [_meeting(m, admin) for m in app.meetings.all()],
        'agreements': [_agreement(a) for a in app.agreements.all()],
        'trial': {'start': app.trial_start, 'end': app.trial_end, 'billing_effective': app.billing_effective},
    }
    if admin:
        data['organization_id'] = str(app.organization_id) if app.organization_id else None
        data['review_blockers'] = services.review_blockers(app) if app.status == 'under_review' else []
        data['approval_blockers'] = services.approval_blockers(app) if app.status == 'agreements_pending' else []
        data['history'] = [{'action': e.action, 'at': e.occurred_at, 'after': e.after_data,
                            'by': e.acting_membership.user.full_name if e.acting_membership_id else 'Applicant / system'}
                           for e in ActivityEvent.objects.filter(entity_type='firm_application', entity_id=app.id)
                           .select_related('acting_membership__user').order_by('occurred_at')]
    return data


def summary_json(app):
    return {'id': str(app.id), 'application_number': app.application_number, 'firm_name': app.firm_name,
            'region': app.region, 'status': app.status, 'status_label': app.get_status_display(),
            'submitted_at': app.submitted_at, 'advocates': app.advocates.count(),
            'trial_end': app.trial_end}


def options_json():
    return {
        'practice_areas': [{'code': a.code, 'name_en': a.name_en, 'name_sw': a.name_sw} for a in PracticeArea.objects.all()],
        'required_documents': [{'type': t, 'label': DocType(t).label, 'multiple': t == DocType.OFFICE_PHOTOS}
                               for t in REQUIRED_FIRM_DOCS],
        'agreements': [{'type': t, 'label': AgreementType(t).label} for t in AgreementType.values],
        'max_primary_areas': 5,
    }
