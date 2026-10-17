from django.urls import path

from . import api

urlpatterns = [
    # applicant (public, secret link token)
    path('onboarding/options/', api.options),
    path('onboarding/applications/', api.create_application),
    path('onboarding/applications/<uuid:app_id>/', api.application_detail),
    path('onboarding/applications/<uuid:app_id>/documents/', api.upload_document),
    path('onboarding/applications/<uuid:app_id>/documents/<uuid:doc_id>/', api.delete_document),
    path('onboarding/applications/<uuid:app_id>/documents/<uuid:doc_id>/download/', api.applicant_download),
    path('onboarding/applications/<uuid:app_id>/submit/', api.submit_application),

    # CLC admin
    path('onboarding/admin/applications/', api.admin_list),
    path('onboarding/admin/applications/create/', api.admin_create_application),
    path('onboarding/admin/applications/<uuid:app_id>/submit-approved/', api.admin_submit_and_approve),
    path('onboarding/admin/compliance/', api.admin_compliance),
    path('onboarding/admin/applications/<uuid:app_id>/', api.admin_detail),
    path('onboarding/admin/applications/<uuid:app_id>/start-review/', api.admin_start_review),
    path('onboarding/admin/applications/<uuid:app_id>/documents/<uuid:doc_id>/review/', api.admin_review_document),
    path('onboarding/admin/applications/<uuid:app_id>/documents/<uuid:doc_id>/download/', api.admin_download),
    path('onboarding/admin/applications/<uuid:app_id>/requests/', api.admin_request_changes),
    path('onboarding/admin/applications/<uuid:app_id>/complete-review/', api.admin_complete_review),
    path('onboarding/admin/applications/<uuid:app_id>/meetings/', api.admin_add_meeting),
    path('onboarding/admin/applications/<uuid:app_id>/meetings/<uuid:meeting_id>/', api.admin_update_meeting),
    path('onboarding/admin/applications/<uuid:app_id>/agreements/<str:agreement_type>/upload/', api.admin_upload_agreement),
    path('onboarding/admin/applications/<uuid:app_id>/agreements/<str:agreement_type>/download/', api.admin_download_agreement),
    path('onboarding/admin/applications/<uuid:app_id>/approve/', api.admin_approve),
    path('onboarding/admin/applications/<uuid:app_id>/reject/', api.admin_reject),
    path('onboarding/admin/applications/<uuid:app_id>/suspend/', api.admin_suspend),
    path('onboarding/admin/applications/<uuid:app_id>/reinstate/', api.admin_reinstate),
    path('onboarding/admin/applications/<uuid:app_id>/activate/', api.admin_activate),
]
