from django.urls import path

from . import api

urlpatterns = [
    path('chatbot/node-types/', api.node_types),
    path('chatbot/flows/', api.flows),
    path('chatbot/flows/<uuid:flow_id>/', api.flow_detail),
    path('chatbot/flows/<uuid:flow_id>/draft/', api.save_draft),
    path('chatbot/flows/<uuid:flow_id>/validate/', api.validate_flow),
    path('chatbot/flows/<uuid:flow_id>/publish/', api.publish),
    path('chatbot/flows/<uuid:flow_id>/versions/', api.versions),
    path('chatbot/flows/<uuid:flow_id>/versions/<uuid:version_id>/restore/', api.restore_version),
    path('chatbot/flows/<uuid:flow_id>/simulate/', api.simulate),
    path('chatbot/media/', api.media),
    path('chatbot/media/<uuid:asset_id>/', api.media_detail),
    path('chatbot/media/<uuid:asset_id>/file/', api.media_file),
    path('chatbot/settings/', api.bot_settings),
    path('chatbot/whatsapp/', api.whatsapp_connections),
    path('chatbot/whatsapp/use-env/', api.whatsapp_connection_use),
    path('chatbot/whatsapp/test/', api.whatsapp_connection_test),
    path('chatbot/whatsapp/<uuid:connection_id>/', api.whatsapp_connection),
    path('chatbot/whatsapp/<uuid:connection_id>/use/', api.whatsapp_connection_use),
    path('chatbot/whatsapp/<uuid:connection_id>/test/', api.whatsapp_connection_test),
    path('whatsapp/webhook/', api.webhook),
]
