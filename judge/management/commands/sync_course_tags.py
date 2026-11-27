from django.core.management.base import BaseCommand
from django.db import transaction

from judge.utils.course_tags import sync_library_course_tags


class Command(BaseCommand):
    help = (
        "Tag the practice course of every exam and division in the exam library with "
        "its exam, division, school level and region. Only adds tags; safe to rerun."
    )

    def handle(self, *args, **options):
        with transaction.atomic():
            added = sync_library_course_tags()
        self.stdout.write(self.style.SUCCESS(f"Added {added} course tags."))
