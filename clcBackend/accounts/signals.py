from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User
from .services import ensure_admin_membership


@receiver(post_save, sender=User)
def superuser_is_clc_admin(sender, instance, **kwargs):
    if instance.is_superuser and instance.is_active:
        ensure_admin_membership(instance)
