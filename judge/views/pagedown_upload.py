"""Image uploads from the Markdown editor, shrunk before django-pagedown stores them."""

from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from pagedown.views import image_upload_view

from judge.utils.image_resize import shrink_upload


@login_required
@csrf_exempt
def image_upload(request):
    image = request.FILES.get("image") if request.method == "POST" else None
    if image is not None:
        smaller = shrink_upload(image)
        if smaller is not None:
            request.FILES["image"] = smaller
    return image_upload_view(request)
