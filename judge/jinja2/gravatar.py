import hashlib

from django.conf import settings
from django.urls import reverse
from django.utils.http import urlencode

from judge.models import Profile
from judge.utils.unicode import utf8bytes
from . import registry


@registry.function
def gravatar(profile_id, size=80):
    profile = Profile(id=profile_id)
    is_muted = profile.get_mute()

    if not is_muted:
        profile_image_url = profile.get_profile_image_url()
        if profile_image_url:
            return profile_image_url

    email = profile.get_email()
    digest = hashlib.md5(utf8bytes(email.strip().lower())).hexdigest()

    if not settings.USE_GRAVATAR:
        # Served by judge.views.avatar as a local identicon. The size is not in
        # the URL: the image is SVG and scales, and every call site already
        # constrains it in markup or CSS.
        return reverse("identicon", args=(digest,))

    gravatar_url = "//www.gravatar.com/avatar/" + digest + "?"
    args = {"d": "identicon", "s": str(size)}
    if is_muted:
        args["f"] = "y"
    gravatar_url += urlencode(args)
    return gravatar_url
