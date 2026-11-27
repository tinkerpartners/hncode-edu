from django.apps import apps
from django.db.models.signals import post_init, post_save

from judge.utils.image_resize import (
    STORED_IMAGES,
    remember_image_names,
    shrink_saved_images,
)


def on_init_remember_images(sender, instance, **kwargs):
    remember_image_names(instance)


def on_save_shrink_images(
    sender, instance, created=False, update_fields=None, raw=False, **kwargs
):
    """Keep uploaded images small, whether they came from a form, a direct upload or admin."""
    # Fixtures (loaddata) store rows as they are and must not touch storage.
    if raw:
        return
    shrink_saved_images(instance, update_fields, created)


for label in STORED_IMAGES:
    model = apps.get_model(label)
    post_init.connect(
        on_init_remember_images, sender=model, dispatch_uid=f"remember_images_{label}"
    )
    post_save.connect(
        on_save_shrink_images, sender=model, dispatch_uid=f"shrink_images_{label}"
    )
