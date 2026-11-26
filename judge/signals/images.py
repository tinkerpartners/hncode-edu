from django.apps import apps
from django.db.models.signals import post_save

from judge.utils.image_resize import STORED_IMAGES, shrink_saved_images


def on_save_shrink_images(sender, instance, update_fields=None, **kwargs):
    """Keep uploaded images small, whether they came from a form, a direct upload or admin."""
    shrink_saved_images(instance, update_fields)


for label in STORED_IMAGES:
    post_save.connect(
        on_save_shrink_images,
        sender=apps.get_model(label),
        dispatch_uid=f"shrink_images_{label}",
    )
