from django.urls import path

from . import advocate_api as api

urlpatterns = [
    path('overview/', api.overview),
    path('subscription/', api.subscription),
    path('appointments/', api.appointments),
    path('matters/', api.matters),
    path('matters/<uuid:case_id>/', api.matter_detail),
    path('matters/<uuid:case_id>/messages/', api.matter_messages),
    path('matters/<uuid:case_id>/chat/take-over/', api.matter_chat_take_over),
    path('matters/<uuid:case_id>/chat/release/', api.matter_chat_release),
    path('matters/<uuid:case_id>/chat/send/', api.matter_chat_send),
    path('matters/<uuid:case_id>/documents/', api.matter_documents),
    path('matters/<uuid:case_id>/documents/<uuid:doc_id>/download/', api.matter_document_download),
    path('matters/<uuid:case_id>/appointments/', api.matter_appointments),
    path('matters/<uuid:case_id>/notes/', api.matter_notes),
    path('matters/<uuid:case_id>/payments/', api.matter_payments),
    path('matters/<uuid:case_id>/start/', api.matter_start),
    path('matters/<uuid:case_id>/ready-to-close/', api.matter_ready_to_close),
]
