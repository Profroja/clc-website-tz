import uuid

from django.db import models


class Lang(models.TextChoices):
    SW = 'sw', 'Swahili'
    EN = 'en', 'English'
    ZH = 'zh', 'Chinese'


class Channel(models.TextChoices):
    WHATSAPP = 'whatsapp', 'WhatsApp'
    WEB = 'web', 'Web'
    EMAIL = 'email', 'Email'
    PHONE = 'phone', 'Phone'
    WALK_IN = 'walk_in', 'Walk-in'


class ActorType(models.TextChoices):
    CLIENT = 'client', 'Client'
    BOT = 'bot', 'Bot'
    STAFF = 'staff', 'Staff'
    SYSTEM = 'system', 'System'


class Priority(models.TextChoices):
    LOW = 'low', 'Low'
    NORMAL = 'normal', 'Normal'
    HIGH = 'high', 'High'
    URGENT = 'urgent', 'Urgent'


class Service(models.Model):
    """Service in the bot menu and on firm profiles."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.TextField(unique=True)  # 'tsa_miner', 'gold_investor', 'deed_poll', 'will', 'general'
    name_sw = models.TextField()
    name_en = models.TextField()
    name_zh = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)  # shown in the bot menu
    sort_order = models.SmallIntegerField(default=0)

    class Meta:
        db_table = 'services'
        ordering = ['sort_order', 'code']

    def __str__(self):
        return self.name_en


class OfficeHours(models.Model):
    weekday = models.SmallIntegerField(primary_key=True)  # 0 = Sunday
    opens_at = models.TimeField()
    closes_at = models.TimeField()

    class Meta:
        db_table = 'office_hours'
        verbose_name_plural = 'office hours'
        constraints = [
            models.CheckConstraint(condition=models.Q(weekday__gte=0, weekday__lte=6), name='office_hours_weekday_range'),
            models.CheckConstraint(condition=models.Q(closes_at__gt=models.F('opens_at')), name='office_hours_closes_after_opens'),
        ]


class PublicHoliday(models.Model):
    holiday_date = models.DateField(primary_key=True)
    name = models.TextField()

    class Meta:
        db_table = 'public_holidays'


class ReferenceCounter(models.Model):
    """Yearly counters behind matter references: CLC/2026/0014."""
    prefix = models.TextField()
    year = models.SmallIntegerField()
    last_value = models.IntegerField(default=0)

    class Meta:
        db_table = 'reference_counters'
        constraints = [
            models.UniqueConstraint(fields=['prefix', 'year'], name='reference_counters_pk'),
        ]


def package_image_path(instance, filename):
    return f'packages/{instance.id}/{uuid.uuid4().hex}_{filename}'


def package_video_path(instance, filename):
    return f'packages/{instance.id}/{uuid.uuid4().hex}_{filename}'


class LiveSettings(models.Model):
    """Settings of the live podcast (one row). When the YouTube channel goes live, the website shows a popup
    with the stream; viewers chat with CLC live on the WhatsApp number set here."""
    channel_handle = models.CharField(max_length=100, default='@Communitylegalclinic')
    popup_title = models.TextField(blank=True, default='')  # shown in the popup; empty = the YouTube title
    whatsapp_number = models.CharField(max_length=20, blank=True, default='')  # digits only, e.g. 255745118253
    whatsapp_message = models.TextField(default='Habari CLC, nimeunganishwa kwenye Podcast LIVE.')
    manual_live = models.BooleanField(default=False)  # the admin says "we are live" (no YouTube API key needed)
    manual_video_id = models.CharField(max_length=20, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'live_settings'

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]


def media_file_path(instance, filename):
    return f'media/{instance.id}/{uuid.uuid4().hex}_{filename}'


class MediaKind(models.TextChoices):
    PODCAST = 'podcast', 'Podcast'
    MIJADALA = 'mijadala', 'Mijadala (discussion)'
    MACHAPISHO = 'machapisho', 'Machapisho (post)'


class MediaPost(models.Model):
    """A podcast, discussion or post shown in "Podcast, Mijadala & Machapisho" on the website."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=MediaKind.choices)
    title = models.TextField()
    excerpt = models.TextField(blank=True, default='')  # the short summary on the card
    body = models.TextField(blank=True, default='')  # the full text; paragraphs are separated by a blank line
    published_on = models.DateField()
    duration = models.CharField(max_length=12, blank=True, default='')  # e.g. 24:10
    cover = models.FileField(upload_to=media_file_path, max_length=300, null=True, blank=True)
    video = models.FileField(upload_to=media_file_path, max_length=300, null=True, blank=True)  # an uploaded video
    video_id = models.CharField(max_length=20, blank=True, default='')  # or a YouTube video ID
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'media_posts'
        ordering = ['-published_on', '-created_at']

    def __str__(self):
        return self.title


class ServicePackage(models.Model):
    """A package / legal service shown on the CLC website ("Vifurushi na Huduma za Kisheria")."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.TextField()
    description = models.TextField(blank=True, default='')
    items = models.JSONField(default=list, blank=True)  # the services included, one entry per line
    image = models.FileField(upload_to=package_image_path, max_length=300, null=True, blank=True)
    video = models.FileField(upload_to=package_video_path, max_length=300, null=True, blank=True)  # an uploaded video file
    video_id = models.CharField(max_length=20, blank=True, default='')  # or a YouTube video ID
    is_published = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'service_packages'
        ordering = ['sort_order', 'created_at']

    def __str__(self):
        return self.title