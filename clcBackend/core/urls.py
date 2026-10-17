from django.urls import path

from . import api, live_api, media_api, packages_api

urlpatterns = [
    path('admin/overview/', api.admin_overview),
    path('admin/packages/', packages_api.packages),
    path('admin/packages/<uuid:package_id>/', packages_api.package_detail),
    path('admin/media/', media_api.media_list),
    path('admin/media/<uuid:media_id>/', media_api.media_detail),
    path('admin/live/', live_api.live_settings),
    path('live/', live_api.public_live),
    path('packages/', packages_api.public_packages),
    path('packages/<uuid:package_id>/image/', packages_api.package_image),
    path('packages/<uuid:package_id>/video/', packages_api.package_video),
    path('media/', media_api.public_media),
    path('media/<uuid:media_id>/cover/', media_api.media_cover),
    path('media/<uuid:media_id>/video/', media_api.media_video),
]
