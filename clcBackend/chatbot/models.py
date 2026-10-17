"""Data the flow builder keeps besides flows: files the bot can send, and bot-wide settings."""
import uuid

from django.conf import settings
from django.db import models


class MediaKind(models.TextChoices):
    IMAGE = 'image', 'Image'
    DOCUMENT = 'document', 'Document'


class MediaAsset(models.Model):
    """A picture or PDF an admin uploaded for the bot to send (Send file block)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)                  # what admins see, e.g. "TSA proposal (SW)"
    kind = models.CharField(max_length=10, choices=MediaKind.choices)
    file_name = models.CharField(max_length=200)              # what the client sees for a document
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    storage_key = models.CharField(max_length=300)
    sha256 = models.CharField(max_length=64)
    # Meta keeps an uploaded file for 30 days; the id is reused until then.
    meta_media_id = models.CharField(max_length=64, blank=True)
    meta_uploaded_at = models.DateTimeField(null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'bot_media_assets'
        ordering = ['name']


class BotSetting(models.Model):
    """Bot-wide settings, one row per key: business_hours, staff_emails."""
    key = models.CharField(max_length=50, primary_key=True)
    value = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'bot_settings'


class WhatsAppConnection(models.Model):
    """A WhatsApp Cloud API connection an admin added. One is in use at a time; with none in use the .env values apply."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    phone_number_id = models.CharField(max_length=40)
    business_account_id = models.CharField(max_length=40, blank=True, default='')
    api_version = models.CharField(max_length=10, blank=True, default='')
    access_token = models.TextField(blank=True, default='')
    app_secret = models.CharField(max_length=200, blank=True, default='')
    verify_token = models.CharField(max_length=200, blank=True, default='')
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'whatsapp_connections'
        ordering = ['created_at']
