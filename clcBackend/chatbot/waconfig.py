"""The WhatsApp connection (Meta Cloud API) in use: the connection an admin selected in the portal,
and for anything it leaves empty, the .env file."""
from django.conf import settings

SECRETS = ('access_token', 'verify_token', 'app_secret')
PLAIN = ('phone_number_id', 'business_account_id', 'api_version')
FIELDS = PLAIN + SECRETS
ENV = {'phone_number_id': 'WHATSAPP_PHONE_NUMBER_ID', 'access_token': 'WHATSAPP_TOKEN', 'verify_token': 'WHATSAPP_VERIFY_TOKEN',
       'app_secret': 'META_APP_SECRET', 'api_version': 'GRAPH_API_VERSION'}


def active():
    from .models import WhatsAppConnection
    return WhatsAppConnection.objects.filter(is_active=True).first()


def env(name):
    return getattr(settings, ENV.get(name, ''), '') or ''


def get(name):
    conn = active()
    return (getattr(conn, name, '') if conn else '') or env(name)


def source(name):
    conn = active()
    if conn and getattr(conn, name, ''):
        return 'connection'
    return 'env' if env(name) else None


def all_values(name):
    """Every known value of a field (all saved connections and .env): the webhook accepts a call that matches any of them."""
    from .models import WhatsAppConnection
    values = {getattr(c, name) for c in WhatsAppConnection.objects.all()} | {env(name)}
    return {v for v in values if v}
