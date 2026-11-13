import json
from functools import partial
from uuid import UUID

from celery.result import AsyncResult
from django.core.exceptions import PermissionDenied
from django.http import (
    Http404,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _

from judge.tasks import failure, progress, success
from judge.utils.celery import redirect_to_task_status
from judge.utils.permissions import can_use_ai_features

MARKDOWN_ERRORS = {
    "not_configured": _("AI chưa được cấu hình. Quản trị viên cần thêm POE_API_KEY."),
    "credentials": _("Poe từ chối xác thực. Quản trị viên cần kiểm tra API key."),
    "quota": _("Poe đã hết hạn mức hoặc đang giới hạn yêu cầu. Vui lòng thử lại sau."),
    "timeout": _("Poe phản hồi quá lâu. Vui lòng thử lại."),
    "problem_missing": _("Bài tập không còn tồn tại."),
    "no_result": _("AI chưa tạo được bản đề xuất Markdown."),
    "provider": _("Không nhận được phản hồi từ Poe. Vui lòng thử lại."),
}


def get_task_status(task_id, user):
    result = AsyncResult(task_id)
    info = result.result
    if result.state == "PROGRESS":
        return {
            "code": "PROGRESS",
            "done": info["done"],
            "total": info["total"],
            "stage": info["stage"],
        }
    elif result.state == "SUCCESS":
        # Return result data if it's a dict, otherwise just success code
        if isinstance(info, dict):
            if "_markdown_owner" in info:
                if not can_use_ai_features(user) or (
                    not user.is_superuser and user.pk != info["_markdown_owner"]
                ):
                    raise Http404()
                if not info["success"]:
                    return {
                        "code": "SUCCESS",
                        "success": False,
                        "error": str(
                            MARKDOWN_ERRORS.get(
                                info.get("error_code"), MARKDOWN_ERRORS["provider"]
                            )
                        ),
                    }
                return {
                    "code": "SUCCESS",
                    "success": True,
                    "improved_markdown": info["improved_markdown"],
                }
            return {"code": "SUCCESS", **info}
        return {"code": "SUCCESS"}
    elif result.state == "FAILURE":
        return {"code": "FAILURE", "error": str(info)}
    else:
        return {"code": "WORKING"}


def task_status(request, task_id):
    try:
        task_id = str(UUID(task_id))
    except ValueError:
        raise Http404()

    redirect = request.GET.get("redirect")
    if not url_has_allowed_host_and_scheme(
        redirect, allowed_hosts={request.get_host()}
    ):
        redirect = None

    status = get_task_status(task_id, request.user)
    if status["code"] == "SUCCESS" and redirect:
        return HttpResponseRedirect(redirect)

    return render(
        request,
        "task_status.html",
        {
            "task_id": task_id,
            "task_status": json.dumps(status),
            "message": request.GET.get("message", ""),
            "redirect": redirect or "",
        },
    )


def task_status_ajax(request):
    if "id" not in request.GET:
        return HttpResponseBadRequest(
            'Need to pass GET parameter "id"', content_type="text/plain"
        )
    try:
        status = get_task_status(str(UUID(request.GET["id"])), request.user)
    except ValueError:
        return JsonResponse({"error": "Invalid task ID"}, status=400)
    except Http404:
        return JsonResponse({"error": "Task not found"}, status=404)
    response = JsonResponse(status)
    response["Cache-Control"] = "private, no-store"
    return response


def demo_task(request, task, message):
    if not request.user.is_superuser:
        raise PermissionDenied()
    result = task.delay()
    return redirect_to_task_status(result, message=message, redirect=reverse("home"))


demo_success = partial(
    demo_task, task=success, message="Running example task that succeeds..."
)
demo_failure = partial(
    demo_task, task=failure, message="Running example task that fails..."
)
demo_progress = partial(
    demo_task, task=progress, message="Running example task that waits 10 seconds..."
)
