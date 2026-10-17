from django.db import migrations

AREAS = [
    ('land_property', 'Land & Property', 'Ardhi na Mali'),
    ('family', 'Family', 'Familia'),
    ('probate', 'Probate & Succession', 'Mirathi na Urithi'),
    ('employment', 'Employment & Labour', 'Ajira na Kazi'),
    ('mining', 'Mining', 'Madini'),
    ('corporate', 'Corporate & Commercial', 'Makampuni na Biashara'),
    ('tax', 'Tax', 'Kodi'),
    ('criminal', 'Criminal', 'Jinai'),
    ('civil_litigation', 'Civil Litigation', 'Mashauri ya Madai'),
    ('banking', 'Banking & Finance', 'Benki na Fedha'),
    ('ip', 'Intellectual Property', 'Haki Miliki'),
    ('immigration', 'Immigration', 'Uhamiaji'),
    ('human_rights', 'Human Rights', 'Haki za Binadamu'),
    ('investment', 'Investment', 'Uwekezaji'),
    ('real_estate', 'Real Estate', 'Mali Isiyohamishika'),
    ('debt_recovery', 'Debt Recovery', 'Ukusanyaji wa Madeni'),
    ('adr', 'ADR (Mediation & Arbitration)', 'Usuluhishi na Upatanishi'),
]


def seed(apps, schema_editor):
    PracticeArea = apps.get_model('onboarding', 'PracticeArea')
    for i, (code, en, sw) in enumerate(AREAS):
        PracticeArea.objects.update_or_create(code=code, defaults={'name_en': en, 'name_sw': sw, 'sort_order': i})


class Migration(migrations.Migration):

    dependencies = [
        ('onboarding', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
