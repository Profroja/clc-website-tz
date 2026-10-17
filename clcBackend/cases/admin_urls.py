from django.urls import path

from . import admin_api as api

urlpatterns = [
    path('matters/', api.matters),
    path('matters/<uuid:case_id>/', api.matter_detail),
    path('matters/<uuid:case_id>/close/', api.close_matter),
    path('matters/<uuid:case_id>/delete/', api.delete_matter),
    path('matters/<uuid:case_id>/assign/', api.assign),
    path('matters/<uuid:case_id>/documents/<uuid:doc_id>/download/', api.matter_document_download),
    path('advocates/', api.advocates),
]
