"""
Storage-agnostic upload handler for direct uploads.

Generates presigned URLs for S3/R2 or signed tokens for local storage,
allowing clients to upload files directly without going through Django.
"""

import hashlib
import hmac
import time

from django.apps import apps
from django.conf import settings
from django.core.files.storage import default_storage
from django.urls import reverse

from judge.utils.files import generate_secure_filename


def get_field_storage(model_name, field_name):
    """
    Return the storage the target FileField actually reads from.

    Direct uploads used to assume ``default_storage`` everywhere. That is wrong for
    any field declaring its own ``storage=`` -- notably ``Problem.pdf_description``,
    which lives in ``problem_data_storage`` (the problem data tree), not MEDIA_ROOT.
    Writing the file to one storage while the field and its serving view read from
    another produces an upload that "succeeds" and then 404s forever.

    Falls back to ``default_storage`` when the field cannot be resolved, which keeps
    every pre-existing caller (profile images, pagedown uploads, ...) unchanged.
    """
    if not model_name or not field_name:
        return default_storage
    try:
        app_label, model = model_name.split(".")
        field = apps.get_model(app_label, model)._meta.get_field(field_name)
    except Exception:
        return default_storage
    return getattr(field, "storage", None) or default_storage


class UploadHandler:
    """
    Storage-agnostic upload handler.
    Automatically detects S3 vs local and returns appropriate upload config.
    """

    # Token expiry in seconds
    TOKEN_EXPIRY = 3600  # 1 hour

    @classmethod
    def get_upload_config(
        cls,
        profile,
        upload_to,
        filename,
        content_type,
        file_size,
        max_size=None,
        prefix=None,
        object_id=None,
        model_name="",
        field_name="",
    ):
        """
        Returns upload configuration for any storage backend.

        Args:
            profile: Profile object
            upload_to: Storage path prefix (e.g., 'profile_images', 'pagedown_images')
            filename: Original filename
            content_type: MIME type of the file
            file_size: Size of the file in bytes
            max_size: Maximum allowed file size (optional)
            prefix: Filename prefix (e.g., 'user', 'organization', 'course')
            object_id: ID of the object being updated (optional)
            model_name: Target model as 'app_label.ModelName' (optional)
            field_name: Target FileField name (optional)

        The file is written to the *target field's own storage*, not blindly to
        default_storage -- see get_field_storage().

        Returns:
            dict: Upload configuration with keys:
                - upload_url: Where to send the file
                - method: HTTP method to use
                - fields: Additional form fields (S3) or empty (local)
                - file_key: Final storage path
                - file_url: Public URL after upload
                - token: Signed token for local uploads (only for local)
        """
        if max_size and file_size > max_size:
            raise ValueError(f"File size exceeds maximum allowed ({max_size} bytes)")

        # Generate filename: {prefix}_{object_id}_{random}.ext or {prefix}_{random}.ext
        if prefix and object_id:
            full_prefix = f"{prefix}_{object_id}"
        elif prefix:
            full_prefix = prefix
        else:
            full_prefix = None
        secure_filename = generate_secure_filename(filename, prefix=full_prefix)
        file_key = f"{upload_to}/{secure_filename}"

        storage = get_field_storage(model_name, field_name)

        if cls._is_s3_storage(storage):
            return cls._get_s3_config(
                profile=profile,
                file_key=file_key,
                content_type=content_type,
                file_size=file_size,
                max_size=max_size,
                storage=storage,
            )
        else:
            return cls._get_local_config(
                profile=profile,
                file_key=file_key,
                content_type=content_type,
                file_size=file_size,
                max_size=max_size,
                storage=storage,
                model_name=model_name,
                field_name=field_name,
            )

    @classmethod
    def _is_s3_storage(cls, storage=None):
        """Check if the given storage is S3-compatible."""
        return hasattr(storage or default_storage, "bucket")

    @classmethod
    def _get_s3_config(
        cls, profile, file_key, content_type, file_size, max_size, storage=None
    ):
        """Generate presigned PUT URL for S3/R2 direct upload."""
        storage = storage or default_storage
        client = storage.connection.meta.client
        bucket_name = storage.bucket_name

        # Add storage location prefix if configured
        full_key = file_key
        if getattr(storage, "location", None):
            full_key = f"{storage.location}/{file_key}"

        presigned_url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket_name,
                "Key": full_key,
                "ContentType": content_type,
            },
            ExpiresIn=cls.TOKEN_EXPIRY,
        )

        file_url = storage.url(file_key)

        return {
            "upload_url": presigned_url,
            "method": "PUT",
            "fields": {},
            "file_key": file_key,
            "file_url": file_url,
            "content_type": content_type,
            "storage_type": "s3",
        }

    @classmethod
    def _get_local_config(
        cls,
        profile,
        file_key,
        content_type,
        file_size,
        max_size,
        storage=None,
        model_name="",
        field_name="",
    ):
        """
        Generate signed token for local storage upload.

        model_name/field_name ride along in the signed payload so the upload
        endpoint can resolve the *same* storage this config was built for,
        instead of falling back to default_storage.
        """
        storage = storage or default_storage
        # Create a signed token that the local upload endpoint will verify
        # Include max_size for server-side file size validation
        expiry = int(time.time()) + cls.TOKEN_EXPIRY
        max_size_val = max_size or 0
        token_data = (
            f"{profile.id}:{file_key}:{content_type}:{max_size_val}:{expiry}"
            f":{model_name}:{field_name}"
        )
        signature = cls._sign_token(token_data)
        token = f"{token_data}:{signature}"

        # Get the local upload endpoint URL
        upload_url = reverse("direct_upload_local")

        # Get the final URL for the uploaded file
        file_url = storage.url(file_key)

        return {
            "upload_url": upload_url,
            "method": "POST",
            "fields": {},
            "file_key": file_key,
            "file_url": file_url,
            "token": token,
            "storage_type": "local",
        }

    @classmethod
    def _sign_token(cls, data):
        """Sign token data with HMAC-SHA256."""
        secret = settings.SECRET_KEY.encode()
        return hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()

    @classmethod
    def verify_token(cls, token, profile_id):
        """
        Verify a local upload token.

        Returns:
            dict: Token data if valid (profile_id, file_key, content_type, max_size,
                  expiry, model_name, field_name)
            None: If token is invalid or expired

        Accepts the older 5-field payload too, so tokens issued before this fix
        (1h TTL) still complete rather than failing mid-upload across a deploy.
        """
        try:
            parts = token.rsplit(":", 1)
            if len(parts) != 2:
                return None

            token_data, signature = parts

            # Verify signature
            expected_signature = cls._sign_token(token_data)
            if not hmac.compare_digest(signature, expected_signature):
                return None

            # Parse token data
            data_parts = token_data.split(":")
            if len(data_parts) == 5:
                # Legacy payload issued before storage was threaded through.
                data_parts = data_parts + ["", ""]
            if len(data_parts) != 7:
                return None

            (
                token_profile_id,
                file_key,
                content_type,
                max_size,
                expiry,
                model_name,
                field_name,
            ) = data_parts
            expiry = int(expiry)
            max_size = int(max_size)

            # Verify user ID matches
            if int(token_profile_id) != profile_id:
                return None

            # Verify not expired
            if time.time() > expiry:
                return None

            return {
                "profile_id": int(token_profile_id),
                "file_key": file_key,
                "content_type": content_type,
                "max_size": max_size,
                "expiry": expiry,
                "model_name": model_name,
                "field_name": field_name,
            }

        except (ValueError, AttributeError):
            return None
