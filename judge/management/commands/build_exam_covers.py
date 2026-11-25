from django.core.management.base import BaseCommand

from judge.models import ExamSeries
from judge.utils.exam_covers import build_cover_thumbnail


class Command(BaseCommand):
    help = "Build missing WebP thumbnails for exam library covers."

    def handle(self, *args, **options):
        urls = (
            ExamSeries.objects.exclude(cover_url="")
            .order_by()
            .values_list("cover_url", flat=True)
            .distinct()
        )
        ready = sum(build_cover_thumbnail(url) for url in urls)
        self.stdout.write(f"{ready}/{len(urls)} covers have a thumbnail")
