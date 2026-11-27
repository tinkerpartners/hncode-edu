from django.apps import apps
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from judge.utils.image_resize import (
    BACKGROUND_IMAGE_MAX_WIDTH,
    STORED_IMAGES,
    checked_key,
    shrink_image_bytes,
    shrink_stored_image,
)
from judge.utils.storage_helpers import storage_listdir
from judge.utils.theme import (
    SAMPLE_BACKGROUNDS_PREFIX,
    invalidate_sample_backgrounds_cache,
)

# Files that no model field points at, only a folder.
SAMPLE_BACKGROUNDS = "sample_backgrounds"
FIELDS = [
    f"{label.split('.')[1]}.{field}"
    for label, fields in STORED_IMAGES.items()
    for field in fields
] + [SAMPLE_BACKGROUNDS]


class Command(BaseCommand):
    help = (
        "Shrink existing uploaded images (avatars, backgrounds, group logos and covers, "
        "course covers, the home page banner, sample backgrounds) in place, keeping "
        "file name and format. Reports only, unless --apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Rewrite the files. Without it, only report what would change.",
        )
        parser.add_argument(
            "--field",
            action="append",
            choices=FIELDS,
            help="Only this kind of image; may be repeated. Default: all of them.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        wanted = set(options["field"] or FIELDS)
        totals = [0, 0, 0]  # checked, shrunk, bytes saved
        for label, fields in STORED_IMAGES.items():
            model = apps.get_model(label)
            for field, limits in fields.items():
                if f"{model.__name__}.{field}" in wanted:
                    names = (
                        model.objects.exclude(**{field: ""})
                        .exclude(**{f"{field}__isnull": True})
                        .values_list(field, flat=True)
                        .distinct()
                        .iterator()
                    )
                    self.shrink_names(
                        f"{model.__name__}.{field}", names, limits, apply, totals
                    )
        if SAMPLE_BACKGROUNDS in wanted:
            _, files = storage_listdir(default_storage, SAMPLE_BACKGROUNDS_PREFIX)
            names = [f"{SAMPLE_BACKGROUNDS_PREFIX}/{f}" for f in files]
            limits = {"max_width": BACKGROUND_IMAGE_MAX_WIDTH}
            self.shrink_names(SAMPLE_BACKGROUNDS, names, limits, apply, totals)
            if apply:
                invalidate_sample_backgrounds_cache()
        verb = "Shrank" if apply else "Would shrink"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {totals[1]} of {totals[0]} images in total, "
                f"saving {totals[2] / 1024 / 1024:.1f} MB."
            )
        )
        if not apply and totals[1]:
            self.stdout.write("Run again with --apply to rewrite the files.")

    def shrink_names(self, kind, names, limits, apply, totals):
        checked = shrunk = missing = failed = saved = 0
        for name in names:
            checked += 1
            if not default_storage.exists(name):
                missing += 1
                continue
            try:
                if apply:
                    gained = shrink_stored_image(name, **limits)
                    cache.set(checked_key(name), True, 30 * 24 * 3600)
                else:
                    with default_storage.open(name, "rb") as f:
                        data = f.read()
                    result = shrink_image_bytes(data, **limits)
                    gained = len(data) - len(result[0]) if result else 0
            except Exception as e:
                failed += 1
                self.stderr.write(f"{name}: {e}")
                continue
            if gained:
                shrunk += 1
                saved += gained
                self.stdout.write(f"{name}: -{gained // 1024} KB")
        self.stdout.write(
            f"{kind}: {shrunk} of {checked} to shrink, "
            f"{saved / 1024 / 1024:.1f} MB. Missing files: {missing}. Errors: {failed}."
        )
        totals[0] += checked
        totals[1] += shrunk
        totals[2] += saved
