import json
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand

from core.models import ServicePackage


class Command(BaseCommand):
    help = ('Load the five packages that were written into the website (in each package\'s original language) '
            'so they can be managed on Admin > Packages & services. Packages that already exist are skipped.')

    def add_arguments(self, parser):
        parser.add_argument('--images-dir', default=str(Path(settings.BASE_DIR).parent / 'clc-website' / 'public'),
                            help='Folder that contains the website images (the "public" folder of the frontend)')

    def handle(self, *args, **opts):
        fixture = Path(__file__).resolve().parents[2] / 'fixtures' / 'site_packages.json'
        public = Path(opts['images_dir'])
        added = 0
        for order, row in enumerate(json.loads(fixture.read_text(encoding='utf-8')), start=1):
            if ServicePackage.objects.filter(title=row['title']).exists():
                continue
            pkg = ServicePackage(title=row['title'], description=row['description'], items=row['items'], sort_order=order)
            image = public / row['image'].lstrip('/')
            if image.exists():
                with image.open('rb') as f:
                    pkg.image.save(image.name, File(f), save=False)
            else:
                self.stdout.write(self.style.WARNING(f'Image not found, loaded without it: {image}'))
            pkg.save()
            added += 1
        self.stdout.write(f'{added} package(s) added.')
