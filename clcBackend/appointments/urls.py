from django.urls import path

from . import api

urlpatterns = [
    path('appointments/options/', api.options),
    path('appointments/slots/', api.slots),
    path('appointments/', api.book),
    path('admin/appointments/', api.admin_list),
    path('admin/appointments/<uuid:appointment_id>/', api.admin_update),
]
