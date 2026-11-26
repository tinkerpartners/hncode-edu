from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from judge.models import Profile
from judge.utils.image_resize import (
    PROFILE_IMAGE_MAX_SIDE,
    shrink_image_bytes,
    shrink_stored_image,
)


class Command(BaseCommand):
    help = (
        "Shrink existing profile images to at most %dpx, in place (same file name "
        "and format). Reports only, unless --apply is given." % PROFILE_IMAGE_MAX_SIDE
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Rewrite the files. Without it, only report what would change.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        names = (
            Profile.objects.exclude(profile_image="")
            .exclude(profile_image__isnull=True)
            .values_list("profile_image", flat=True)
            .distinct()
        )
        checked = shrunk = missing = failed = 0
        saved = 0
        for name in names.iterator():
            checked += 1
            if not default_storage.exists(name):
                missing += 1
                continue
            try:
                if apply:
                    gained = shrink_stored_image(name)
                    cache.set(f"profile_image_checked:{name}", True, 30 * 24 * 3600)
                else:
                    with default_storage.open(name, "rb") as f:
                        data = f.read()
                    result = shrink_image_bytes(data, max_side=PROFILE_IMAGE_MAX_SIDE)
                    gained = len(data) - len(result[0]) if result else 0
            except Exception as e:
                failed += 1
                self.stderr.write(f"{name}: {e}")
                continue
            if gained:
                shrunk += 1
                saved += gained
                self.stdout.write(f"{name}: -{gained // 1024} KB")
        verb = "Shrank" if apply else "Would shrink"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {shrunk} of {checked} images, saving {saved / 1024 / 1024:.1f} MB. "
                f"Missing files: {missing}. Errors: {failed}."
            )
        )
        if not apply and shrunk:
            self.stdout.write("Run again with --apply to rewrite the files.")
