import os
import shutil
import tempfile
from io import BytesIO, StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image

from judge.models import Profile
from judge.utils.image_resize import (
    CONTENT_IMAGE_MAX_WIDTH,
    PROFILE_IMAGE_MAX_SIDE,
    shrink_image_bytes,
    shrink_upload,
)


def photo(width, height, fmt="PNG", alpha=False):
    """A noisy picture, so PNG compresses it about as badly as a real photo."""
    mode = "RGBA" if alpha else "RGB"
    img = Image.frombytes(mode, (width, height), os.urandom(width * height * len(mode)))
    if alpha:
        img.putpixel((0, 0), (0, 0, 0, 0))
    buf = BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def size_of(data):
    with Image.open(BytesIO(data)) as img:
        return img.format, img.size


class ShrinkImageBytesTest(TestCase):
    def test_small_image_is_left_alone(self):
        self.assertIsNone(shrink_image_bytes(photo(200, 100), max_side=320))

    def test_avatar_keeps_format_and_fits_the_box(self):
        out, fmt = shrink_image_bytes(
            photo(1500, 1000), max_side=PROFILE_IMAGE_MAX_SIDE
        )
        self.assertEqual(fmt, "PNG")
        self.assertEqual(size_of(out), ("PNG", (320, 213)))

    def test_jpeg_is_resized_by_width(self):
        out, fmt = shrink_image_bytes(photo(2400, 1200, "JPEG"), max_width=1600)
        self.assertEqual(size_of(out), ("JPEG", (1600, 800)))

    def test_large_opaque_png_becomes_webp_only_when_allowed(self):
        data = photo(700, 500)
        self.assertGreater(len(data), 300 * 1024)
        self.assertIsNone(shrink_image_bytes(data, max_width=1600))
        out, fmt = shrink_image_bytes(data, max_width=1600, allow_webp=True)
        self.assertEqual(size_of(out), ("WEBP", (700, 500)))

    def test_transparent_png_stays_png(self):
        out, fmt = shrink_image_bytes(
            photo(2000, 400, alpha=True), max_width=1600, allow_webp=True
        )
        self.assertEqual(size_of(out), ("PNG", (1600, 320)))

    def test_not_an_image(self):
        self.assertIsNone(shrink_image_bytes(b"not an image", max_side=320))

    def test_upload_gets_the_new_extension(self):
        upload = SimpleUploadedFile("Banner.PNG", photo(2000, 1000), "image/png")
        smaller = shrink_upload(upload)
        self.assertEqual(smaller.name, "Banner.webp")
        self.assertEqual(smaller.content_type, "image/webp")
        self.assertEqual(size_of(smaller.read()), ("WEBP", (1600, 800)))


class StoredImageTestCase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="hn-media-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.user = User.objects.create_user("avatar_owner", password="pw")
        self.profile, _ = Profile.objects.get_or_create(user=self.user)

    def stored(self, name):
        with default_storage.open(name, "rb") as f:
            return size_of(f.read())


class ProfileImageTest(StoredImageTestCase):
    def test_uploaded_avatar_is_shrunk_in_place(self):
        self.profile.profile_image.save("me.png", ContentFile(photo(1500, 1500)))
        self.profile.refresh_from_db()
        name = self.profile.profile_image.name
        self.assertTrue(name.endswith(".png"))
        self.assertEqual(self.stored(name), ("PNG", (320, 320)))

    def test_saving_other_fields_does_not_touch_the_avatar(self):
        name = default_storage.save(
            "profile_images/old.png", ContentFile(photo(900, 900))
        )
        Profile.objects.filter(pk=self.profile.pk).update(profile_image=name)
        self.profile.refresh_from_db()
        self.profile.about = "Xin chào"
        self.profile.save(update_fields=["about"])
        self.assertEqual(self.stored(name), ("PNG", (900, 900)))

    def test_command_reports_then_applies(self):
        name = default_storage.save(
            "profile_images/old.png", ContentFile(photo(900, 600))
        )
        Profile.objects.filter(pk=self.profile.pk).update(profile_image=name)

        out = StringIO()
        call_command("shrink_profile_images", stdout=out)
        self.assertIn("Would shrink 1 of 1", out.getvalue())
        self.assertEqual(self.stored(name), ("PNG", (900, 600)))

        out = StringIO()
        call_command("shrink_profile_images", "--apply", stdout=out)
        self.assertIn("Shrank 1 of 1", out.getvalue())
        self.assertEqual(self.stored(name), ("PNG", (320, 213)))


@mock.patch("pagedown.views.IMAGE_UPLOAD_ENABLED", True)
class PagedownUploadTest(StoredImageTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def upload(self, name, data):
        response = self.client.post(
            "/pagedown/image-upload/",
            {"image": SimpleUploadedFile(name, data, "image/png")},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"], body)
        return body["url"].split("/media/", 1)[1]

    def test_large_photo_is_resized_and_converted(self):
        name = self.upload("banner.png", photo(2000, 1000))
        self.assertTrue(name.endswith("banner.webp"), name)
        self.assertEqual(self.stored(name), ("WEBP", (CONTENT_IMAGE_MAX_WIDTH, 800)))

    def test_small_image_is_stored_unchanged(self):
        data = photo(120, 80)
        name = self.upload("icon.png", data)
        self.assertTrue(name.endswith("icon.png"), name)
        with default_storage.open(name, "rb") as f:
            self.assertEqual(f.read(), data)

    def test_login_is_still_required(self):
        self.client.logout()
        response = self.client.post(
            "/pagedown/image-upload/",
            {"image": SimpleUploadedFile("x.png", photo(50, 50), "image/png")},
        )
        self.assertEqual(response.status_code, 302)
