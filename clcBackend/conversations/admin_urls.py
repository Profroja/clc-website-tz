from django.urls import path

from . import admin_api as api

urlpatterns = [
    path('conversations/', api.conversations),
    path('conversations/<uuid:conversation_id>/', api.conversation_detail),
    path('conversations/<uuid:conversation_id>/take-over/', api.take_over),
    path('conversations/<uuid:conversation_id>/release/', api.release),
    path('conversations/<uuid:conversation_id>/close/', api.close),
    path('conversations/<uuid:conversation_id>/send/', api.send),
]
