from datetime import time

from django.db import migrations

# The hours shown on the website: Mon-Fri 9:00-18:00, Sat 9:00-13:00, closed on Sundays (weekday 0 = Sunday).
HOURS = {1: (time(9), time(18)), 2: (time(9), time(18)), 3: (time(9), time(18)), 4: (time(9), time(18)),
         5: (time(9), time(18)), 6: (time(9), time(13))}


def seed(apps, schema_editor):
    OfficeHours = apps.get_model('core', 'OfficeHours')
    if OfficeHours.objects.exists():  # never overwrite hours somebody already set
        return
    for weekday, (opens, closes) in HOURS.items():
        OfficeHours.objects.create(weekday=weekday, opens_at=opens, closes_at=closes)


class Migration(migrations.Migration):

    dependencies = [
        ('appointments', '0001_initial'),
        ('core', '0005_livesettings'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
