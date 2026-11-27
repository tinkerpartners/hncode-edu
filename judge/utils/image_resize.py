"""Shrink uploaded images so pages do not ship photos far larger than shown."""

import logging
import os
import uuid
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger("judge.image_resize")

# Limits keep roughly twice the largest size each image is shown at, for sharp screens.
# Avatars are shown at most 145px.
PROFILE_IMAGE_MAX_SIDE = 320
# Group logos are shown at most 150px tall, in cards about 250px wide.
ORGANIZATION_IMAGE_MAX_SIDE = 512
# Content images, group covers and the home page banner sit in a column under 800px wide.
CONTENT_IMAGE_MAX_WIDTH = 1600
# Course covers are 150px tall cards, up to about 480px wide on phones.
COURSE_IMAGE_MAX_WIDTH = 960
# Profile backgrounds fill the whole window on desktop.
BACKGROUND_IMAGE_MAX_WIDTH = 1920
# An opaque PNG above this size is almost always a photo or a screenshot of
# one, which WebP stores at a fraction of the size.
PNG_TO_WEBP_MIN_BYTES = 300 * 1024
# Images with more pixels are left as they are without being decoded. A few
# hundred KB of PNG can hold a 9400x9400 picture that takes about 700 MB of
# memory to decode; real photos stay well under this (a 48 MP phone photo is
# about 48M pixels, 8000x5000 is 40M).
MAX_IMAGE_PIXELS = 50_000_000

_SUPPORTED = {"JPEG", "PNG", "WEBP"}
_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def _has_alpha(img):
    if img.mode in ("RGBA", "LA", "PA"):
        return img.getchannel("A").getextrema()[0] < 255
    return img.mode == "P" and "transparency" in img.info


def _encode(img, fmt, icc_profile=None):
    buf = BytesIO()
    # Keep the colour profile, so wide-gamut (Display P3) photos keep their colours.
    extra = {"icc_profile": icc_profile} if icc_profile else {}
    if fmt == "JPEG":
        # CMYK is stored as CMYK: converting without the profile shifts colours.
        if img.mode not in ("RGB", "L", "CMYK"):
            img = img.convert("RGB")
        img.save(buf, "JPEG", quality=85, optimize=True, progressive=True, **extra)
    elif fmt == "WEBP":
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        img.save(buf, "WEBP", quality=85, method=4, **extra)
    else:
        img.save(buf, "PNG", optimize=True, **extra)
    return buf.getvalue()


def _resize(img, size):
    """Resize smoothly. Palette and 1-bit images are resized in full colour
    (resizing the palette indexes would make edges jagged), then reduced back
    to a palette so a logo stays small."""
    mode = img.mode
    if mode in ("P", "1"):
        img = img.convert("RGBA" if _has_alpha(img) else "RGB")
    img = img.resize(size, Image.LANCZOS)
    if mode == "P":
        img = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
    return img


def shrink_image_bytes(data, max_side=None, max_width=None, allow_webp=False):
    """Return ``(bytes, format)`` for a smaller copy, or ``None`` to keep the original.

    The format stays the same unless ``allow_webp`` is set and the image is a
    large opaque PNG. Animated images and formats other than JPEG, PNG and WebP
    are left alone, as is any result that would not be smaller.
    """
    try:
        img = Image.open(BytesIO(data))
        fmt = img.format
        if fmt not in _SUPPORTED or getattr(img, "is_animated", False):
            return None
        width, height = img.size
        # Checked before anything decodes the pixels.
        if width * height > MAX_IMAGE_PIXELS:
            logger.warning(
                "Not shrinking a %dx%d image: too many pixels", width, height
            )
            return None
        icc_profile = img.info.get("icc_profile")
        scale = 1.0
        if max_side:
            scale = min(scale, max_side / max(width, height))
        if max_width:
            scale = min(scale, max_width / width)
        resized = scale < 1
        if resized and fmt == "JPEG":
            # Let the decoder skip detail that the resize would throw away.
            img.draft("RGB", (int(width * scale) * 2, int(height * scale) * 2))
        img = ImageOps.exif_transpose(img)
        if resized:
            # exif_transpose may swap the sides; recompute from the current size.
            w, h = img.size
            ratio = min(
                max_side / max(w, h) if max_side else 1.0,
                max_width / w if max_width else 1.0,
            )
            img = _resize(img, (max(1, round(w * ratio)), max(1, round(h * ratio))))
        out_fmt = fmt
        if (
            allow_webp
            and fmt == "PNG"
            and len(data) > PNG_TO_WEBP_MIN_BYTES
            and not _has_alpha(img)
        ):
            out_fmt = "WEBP"
        if not resized and out_fmt == fmt:
            return None
        out = _encode(img, out_fmt, icc_profile)
    except UnidentifiedImageError:
        return None
    except Exception:
        logger.warning("Could not shrink image", exc_info=True)
        return None
    if len(out) >= len(data):
        return None
    return out, out_fmt


def shrink_named_image(name, data, max_width=CONTENT_IMAGE_MAX_WIDTH, allow_webp=True):
    """Return ``(name, bytes, format)`` for a smaller copy, or ``None`` to keep it.

    The name keeps its stem; its extension follows the format when that changes.
    """
    result = shrink_image_bytes(data, max_width=max_width, allow_webp=allow_webp)
    if not result:
        return None
    out, fmt = result
    base, ext = os.path.splitext(name)
    if Image.registered_extensions().get(ext.lower()) != fmt:
        ext = _EXTENSIONS[fmt]
    return base + ext, out, fmt


def shrink_upload(upload, max_width=CONTENT_IMAGE_MAX_WIDTH, allow_webp=True):
    """Return a smaller copy of an uploaded image, or ``None`` to keep it."""
    data = upload.read()
    upload.seek(0)
    result = shrink_named_image(upload.name, data, max_width, allow_webp)
    if not result:
        return None
    name, out, fmt = result
    return SimpleUploadedFile(name, out, content_type=_CONTENT_TYPES[fmt])


def shrink_stored_image(name, max_side=None, max_width=None, storage=None):
    """Shrink a stored image in place, keeping its name and format.

    Returns the number of bytes saved, or 0 when the file was left as it was.
    """
    storage = storage or default_storage
    with storage.open(name, "rb") as f:
        data = f.read()
    result = shrink_image_bytes(data, max_side=max_side, max_width=max_width)
    if not result:
        return 0
    out = result[0]
    try:
        path = storage.path(name)
    except NotImplementedError:
        path = None
    if path:
        # Write next to the file and swap it in, so a failure never leaves it missing.
        tmp = f"{path}.{uuid.uuid4().hex}.shrinking"
        with open(tmp, "wb") as f:
            f.write(out)
        os.replace(tmp, path)
    else:
        storage.delete(name)
        storage.save(name, ContentFile(out))
    return len(data) - len(out)


# Uploaded images shrunk in place when their model is saved, and by the
# shrink_uploaded_images command for files stored before this existed.
STORED_IMAGES = {
    "judge.Profile": {
        "profile_image": {"max_side": PROFILE_IMAGE_MAX_SIDE},
        "background_image": {"max_width": BACKGROUND_IMAGE_MAX_WIDTH},
    },
    "judge.Organization": {
        "organization_image": {"max_side": ORGANIZATION_IMAGE_MAX_SIDE},
        "cover_image": {"max_width": CONTENT_IMAGE_MAX_WIDTH},
    },
    "judge.Course": {
        "course_image": {"max_width": COURSE_IMAGE_MAX_WIDTH},
    },
    "judge.HomeHeroSection": {
        "image": {"max_width": CONTENT_IMAGE_MAX_WIDTH},
    },
}
_CHECKED_FOR = 30 * 24 * 3600


def checked_key(name):
    return f"stored_image_checked:{name}"


_LOADED_NAMES = "_stored_image_names"


def _field_name(instance, field):
    value = instance.__dict__.get(field)
    if value is None:
        return None
    return getattr(value, "name", value) or ""


def remember_image_names(instance):
    """Note the image names an instance was loaded with (no file access)."""
    names = {}
    for field in STORED_IMAGES.get(instance._meta.label, {}):
        if field in instance.__dict__:
            names[field] = _field_name(instance, field)
    setattr(instance, _LOADED_NAMES, names)


def shrink_saved_images(instance, update_fields=None, created=False):
    """Shrink the images of a just-saved instance, however they were uploaded.

    Only a new or changed image is read back from storage, so the many saves
    that do not touch an image (a profile joining a contest, say) cost nothing.
    """
    from django.core.cache import cache

    fields = STORED_IMAGES.get(instance._meta.label, {})
    loaded = getattr(instance, _LOADED_NAMES, {})
    for field, limits in fields.items():
        if update_fields is not None and field not in update_fields:
            continue
        value = getattr(instance, field)
        name = value.name if value else ""
        if not created and field in loaded and loaded[field] == name:
            continue
        loaded[field] = name
        if not name or cache.get(checked_key(name)):
            continue
        try:
            shrink_stored_image(name, **limits)
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("Could not shrink %s %s", field, name, exc_info=True)
            continue
        cache.set(checked_key(name), True, _CHECKED_FOR)
