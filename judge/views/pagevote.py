from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import (
    Http404,
    HttpResponseBadRequest,
    HttpResponseForbidden,
)
from django.utils.translation import gettext as _
from django.views.generic.base import TemplateResponseMixin
from django.views.generic.detail import SingleObjectMixin
from django.views.generic import View
from django.conf import settings
from django.http import JsonResponse

from judge.utils.ratelimit import ratelimit
from judge.utils.contribution import get_content_author_profile_ids
from judge.models.pagevote import (
    PageVote,
    VoteService,
)

__all__ = [
    "vote_page",
    "PageVoteDetailView",
    "PageVoteListView",
]


@ratelimit(key="user", rate=settings.RL_VOTE)
@login_required
def vote_page(request):
    """Vote on a page using the PageVote system"""
    try:
        delta = int(request.POST.get("delta"))
    except (TypeError, ValueError):
        return HttpResponseForbidden()
    if delta == -1:
        # A10: Dislike was removed. Old downvotes are kept, but no new ones.
        return HttpResponseBadRequest(
            _("Dislike is no longer available."), content_type="text/plain"
        )
    if delta not in [1, 0]:
        return HttpResponseBadRequest(
            _("Invalid value for delta. It must be 1 or 0."),
            content_type="text/plain",
        )

    if request.method != "POST":
        return HttpResponseForbidden()

    pagevote_id = request.POST.get("id")

    if not pagevote_id:
        return HttpResponseBadRequest(
            _("Missing 'id' parameter."), content_type="text/plain"
        )

    # Ensure the user has solved at least one problem, unless they are staff
    if (
        not request.user.is_staff
        and not request.profile.submission_set.filter(
            points=F("problem__points")
        ).exists()
    ):
        return HttpResponseBadRequest(
            _("You must solve at least one problem before you can vote."),
            content_type="text/plain",
        )

    try:
        pagevote_id = int(pagevote_id)
    except ValueError:
        return HttpResponseBadRequest(
            _("Invalid ID format."), content_type="text/plain"
        )

    try:
        pagevote = PageVote.objects.get(id=pagevote_id)
    except PageVote.DoesNotExist:
        raise Http404(_("The specified PageVote does not exist."))

    # Get the linked object
    linked_object = pagevote.linked_object

    # Prevent self-voting
    author_ids = get_content_author_profile_ids(
        pagevote.content_type, pagevote.object_id
    )
    if request.profile.id in author_ids:
        return HttpResponseBadRequest(
            _("You cannot vote on your own content."),
            content_type="text/plain",
        )

    # Use the VoteService to handle the vote logic
    current_score = VoteService.vote(linked_object, request.user, delta)

    # current_score stays for API compatibility; the page shows like_count.
    return JsonResponse(
        {"current_score": current_score, "like_count": pagevote.like_count()}
    )


class PageVoteDetailView(TemplateResponseMixin, SingleObjectMixin, View):
    pagevote_page = None

    def get_pagevote_page(self):
        if self.pagevote_page is None:
            raise NotImplementedError()
        return self.pagevote_page

    def get_context_data(self, **kwargs):
        context = super(PageVoteDetailView, self).get_context_data(**kwargs)
        context["pagevote"] = self.object.get_or_create_pagevote()
        if self.request.user.is_authenticated and hasattr(self.object, "authors"):
            context["is_own_content"] = self.object.authors.filter(
                id=self.request.profile.id
            ).exists()
        else:
            context["is_own_content"] = False
        return context
