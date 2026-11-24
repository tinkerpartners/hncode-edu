"""Small, cached cover images for the exam library catalog."""

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
    image.save(output, "WEBP", quality=COVER_QUALITY, method=6)
    return default_storage.save(thumb_name, ContentFile(output.getvalue()))


def cover_thumbnail_url(url):
    """URL of a resized WebP copy of a media cover, or the original URL.

    Covers outside this site's media, or any image that cannot be read, are
    returned unchanged so the catalog keeps working.
    """
    if not url:
        return ""
    key = "exam_cover_thumb:" + hashlib.sha1(url.encode()).hexdigest()
    cached = cache.get(key)
    if cached:
        return cached
    result = url
    name = _local_media_name(url)
    if name:
        try:
            if default_storage.exists(name):
                stamp = f"{name}:{default_storage.size(name)}:{COVER_WIDTH}"
                digest = hashlib.sha1(stamp.encode()).hexdigest()[:20]
                thumb_name = f"exam-library/covers/{digest}.webp"
                if not default_storage.exists(thumb_name):
                    thumb_name = _render_thumbnail(name, thumb_name)
                result = default_storage.url(thumb_name)
        except Exception:  # Keep the original image if Pillow or storage fails.
            logger.warning(
                "Cannot build exam cover thumbnail for %s", url, exc_info=True
            )
            result = url
    cache.set(key, result, COVER_CACHE_SECONDS)
    return result
