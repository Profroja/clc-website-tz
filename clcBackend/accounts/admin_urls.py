from django.urls import path

from . import admin_api

urlpatterns = [
    path('users/', admin_api.users),
    path('users/<uuid:membership_id>/', admin_api.edit_user),
    path('users/<uuid:membership_id>/deactivate/', admin_api.deactivate_user),
    path('users/<uuid:membership_id>/activate/', admin_api.activate_user),
    path('users/<uuid:membership_id>/reset-password/', admin_api.reset_password),
    path('firms/', admin_api.registered_firms),
]
