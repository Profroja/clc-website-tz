import json
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand

from core.models import MediaPost


class Command(BaseCommand):
    help = ('Load the six podcasts, discussions and posts that were written into the website (in Swahili) '
            'so they can be managed on Admin > Podcast, Mijadala & Machapisho. Items that already exist are skipped.')

    def add_arguments(self, parser):
        parser.add_argument('--images-dir', default=str(Path(settings.BASE_DIR).parent / 'clc-website' / 'public'),
                            help='Folder that contains the website images (the "public" folder of the frontend)')

    def handle(self, *args, **opts):
        fixture = Path(__file__).resolve().parents[2] / 'fixtures' / 'site_media.json'
        public = Path(opts['images_dir'])
        added = 0
        for row in json.loads(fixture.read_text(encoding='utf-8')):
            if MediaPost.objects.filter(title=row['title']).exists():
                continue
            post = MediaPost(kind=row['kind'], title=row['title'], excerpt=row['excerpt'], body=row['body'],
                             published_on=row['published_on'], duration=row['duration'], video_id=row['youtube'])
            image = public / row['image'].lstrip('/')
            if image.exists():
                with image.open('rb') as f:
                    post.cover.save(image.name, File(f), save=False)
            else:
                self.stdout.write(self.style.WARNING(f'Image not found, loaded without it: {image}'))
            post.save()
            added += 1
        self.stdout.write(f'{added} item(s) added.')
