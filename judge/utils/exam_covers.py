"""Small cover images for the exam library catalog.

Thumbnails are built when a series is saved in admin or by the
``build_exam_covers`` command. Pages only look them up.
"""

import hashlib
import logging
from io import BytesIO
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger("judge.exam_library")

COVER_WIDTH = 720
COVER_QUALITY = 80
COVER_CACHE_SECONDS = 24 * 60 * 60
# A missing thumbnail is looked up again soon, in case it is built elsewhere.
MISSING_CACHE_SECONDS = 10 * 60
BUILD_LOCK_SECONDS = 5 * 60


def _local_media_name(url):
    """Return the storage name for a cover kept in this site's media, if any."""
    parsed = urlparse(url)
    media = urlparse(settings.MEDIA_URL)
    hosts = {host for host in settings.ALLOWED_HOSTS if host and "*" not in host}
    if media.netloc:
        hosts.add(media.netloc)
    if parsed.scheme not in ("", "http", "https"):
        return None
    if parsed.netloc and parsed.netloc.split(":")[0] not in {
        host.lstrip(".").split(":")[0] for host in hosts
    }:
        return None
    media_path = media.path if media.path.endswith("/") else f"{media.path}/"
    if not parsed.path.startswith(media_path):
        return None
    name = unquote(parsed.path[len(media_path) :])
    if not name or ".." in name.split("/"):
        return None
    return name


def _cache_key(url):
    return "exam_cover_thumb:" + hashlib.sha1(url.encode()).hexdigest()


def _thumbnail_name(name):
    stamp = f"{name}:{default_storage.size(name)}:{COVER_WIDTH}"
    digest = hashlib.sha1(stamp.encode()).hexdigest()[:20]
    return f"exam-library/covers/{digest}.webp"


def _render_thumbnail(name, thumb_name):
    from PIL import Image, ImageOps

    with default_storage.open(name, "rb") as source:
        image = Image.open(source)
        image.load()
    image = ImageOps.exif_transpose(image)
    if image.width > COVER_WIDTH:
        height = round(image.height * COVER_WIDTH / image.width)
        image = image.resize((COVER_WIDTH, height), Image.LANCZOS)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    output = BytesIO()
    image.save(output, "WEBP", quality=COVER_QUALITY, method=4)
    saved = default_storage.save(thumb_name, ContentFile(output.getvalue()))
    if saved != thumb_name:
        # Another process stored the same thumbnail first.
        default_storage.delete(saved)


def build_cover_thumbnail(url):
    """Build the WebP copy of a media cover. Returns True if one is available.

    Covers outside this site's media and unreadable images are skipped.
    """
    name = _local_media_name(url) if url else None
    if not name:
        return False
    key = _cache_key(url)
    lock = f"{key}:lock"
    try:
        if not default_storage.exists(name):
            return False
        thumb_name = _thumbnail_name(name)
        if not default_storage.exists(thumb_name):
            if not cache.add(lock, 1, BUILD_LOCK_SECONDS):
                return False
            try:
                _render_thumbnail(name, thumb_name)
            finally:
                cache.delete(lock)
    except Exception:  # The original image stays in use.
        logger.warning("Cannot build exam cover thumbnail for %s", url, exc_info=True)
        return False
    cache.delete(key)
    return True


def cover_thumbnail_url(url):
    """URL of the prepared WebP copy of a media cover, or the original URL.

    Never renders: this runs while the catalog page is being built.
    """
    if not url:
        return ""
    key = _cache_key(url)
    cached = cache.get(key)
    if cached:
        return cached
    result, timeout = url, COVER_CACHE_SECONDS
    name = _local_media_name(url)
    if name:
        timeout = MISSING_CACHE_SECONDS
        try:
            if default_storage.exists(name):
                thumb_name = _thumbnail_name(name)
                if default_storage.exists(thumb_name):
                    result = default_storage.url(thumb_name)
                    timeout = COVER_CACHE_SECONDS
        except Exception:  # Keep the original image if storage fails.
            logger.warning(
                "Cannot look up exam cover thumbnail for %s", url, exc_info=True
            )
    cache.set(key, result, timeout)
    return result
