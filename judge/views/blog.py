from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Case, Exists, Max, OuterRef, Q, When
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.generic import UpdateView, View
from django.views.generic.base import TemplateResponseMixin
from django.views.generic.detail import SingleObjectMixin

from reversion import revisions

from judge.forms import BlogPostEditForm
from judge.models import BlogPost, Comment, Course, HomeHeroSection, Organization
from judge.models.block import get_all_blocked_pairs
from judge.utils.feed import build_home_feed
from judge.utils.views import TitleMixin, generic_message
from judge.views.bookmark import BookMarkDetailView
from judge.views.comment import CommentableMixin
from judge.views.feed import HomeFeedView
from judge.views.pagevote import PageVoteDetailView


class PostList(HomeFeedView):
    model = BlogPost
    paginate_by = 4
    context_object_name = "posts"
    feed_content_template_name = "blog/content.html"
    url_name = "blog_post_list"

    def get(self, request, *args, **kwargs):
        self.show_suggestions = (
            self.template_name == "home.html"
            and request.GET.get("tab") == "suggestions"
        )
        only_content = request.GET.get("only_content")
        if self.show_suggestions:
            self.suggestions_context = {"has_next_page": False, "feed_items": []}
            if request.user.is_authenticated:
                self.ensure_feed_token(request)
                result = build_home_feed(
                    request, cursor_str=request.GET.get("cursor"), include_posts=False
                )
                self.suggestions_context.update(
                    feed_items=result["items"],
                    has_next_page=result["has_next_page"],
                    next_cursor=result["next_cursor"],
                )

            if only_content:
                return render(
                    request, "home/feed-content.html", self.suggestions_context
                )

        if only_content and request.GET.get("cursor") and not request.GET.get("page"):
            return render(
                request,
                self.feed_content_template_name,
                {"posts": [], "has_next_page": False},
            )
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        if self.show_suggestions:
            return BlogPost.objects.none()

        posts = BlogPost.objects.filter(visible=True, publish_on__lte=timezone.now())
        public = Q(is_organization_private=False)
        profile = self.request.profile
        if not profile:
            return posts.filter(public).order_by("-sticky", "-publish_on", "-pk")

        group_posts = BlogPost.organizations.through.objects.filter(
            blogpost_id=OuterRef("pk"),
            organization_id__in=profile.get_organization_ids(),
        )
        public_pin = Case(When(public & Q(sticky=True), then=1), default=0)
        return posts.filter(public | Q(Exists(group_posts))).order_by(
            public_pin.desc(), "-publish_on", "-pk"
        )

    def get_context_data(self, **kwargs):
        context = super(PostList, self).get_context_data(**kwargs)
        context["title"] = self.title or _("Blog posts")
        context["page_type"] = "blog"
        context["blog_cards"] = True
        # Home page only (the /blog/ list shares this view but not the template).
        if self.template_name == "home.html":
            context["feed_content_url"] = reverse("home")
            context["show_suggestions"] = self.show_suggestions
            context["home_hero"] = HomeHeroSection.get_cached_dict()
            context["comment_stream"] = Comment.most_recent(
                self.request.user,
                organization=self.request.organization,
                n=10,
            )
            if self.request.profile:
                context.update(self.get_recent_learning_context())

        if self.show_suggestions:
            context.update(self.suggestions_context)
        else:
            BlogPost.prefetch_organization_ids(*[post.id for post in context["posts"]])

        return context

    def get_recent_learning_context(self):
        profile = self.request.profile
        organization_type = ContentType.objects.get_for_model(Organization)
        blocked_ids = [
            pk
            for type_id, pk in get_all_blocked_pairs(profile)
            if type_id == organization_type.pk
        ]
        return {
            "home_recent_groups": (
                Organization.listed_to(profile)
                .filter(last_vist__profile=profile)
                .exclude(pk__in=blocked_ids)
                .values("pk", "name", "slug")
                .annotate(visited_at=Max("last_vist__last_visit_time"))
                .order_by("-visited_at", "pk")[:2]
            ),
            "home_recent_courses": (
                Course.get_accessible_courses(profile)
                .filter(coursevisit__profile=profile)
                .only("pk", "name", "slug")
                .order_by("-coursevisit__last_visit_time", "-coursevisit__pk")[:2]
            ),
        }

    def get_feed_context(self, object_list):
        BlogPost.prefetch_organization_ids(*[post.id for post in object_list])
        return {"blog_cards": True}


class PostView(
    TitleMixin,
    CommentableMixin,
    PageVoteDetailView,
    BookMarkDetailView,
    TemplateResponseMixin,
    SingleObjectMixin,
    View,
):
    model = BlogPost
    pk_url_kwarg = "id"
    context_object_name = "post"
    template_name = "blog/blog.html"

    def get_title(self):
        return self.object.title

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return self.render_to_response(
            self.get_context_data(
                object=self.object,
            )
        )

    def get_context_data(self, **kwargs):
        context = super(PostView, self).get_context_data(**kwargs)
        context["og_image"] = self.object.og_image
        context["editable_orgs"] = []

        context["organizations"] = self.object.get_organizations()

        if self.request.profile:
            is_author = self.request.profile.id in self.object.get_author_ids()
            for org in context["organizations"]:
                if self.request.profile.can_edit_organization(org):
                    context["editable_orgs"].append(org)
                elif org.can_moderate(self.request.profile):
                    context["editable_orgs"].append(org)
                elif is_author and not self.object.visible:
                    context["editable_orgs"].append(org)

        if self.object.is_editable_by(self.request.user):
            context["num_open_tickets"] = self.object.tickets.filter(
                is_open=True
            ).count()

        context = self.get_comment_context(context)

        return context

    def get_object(self, queryset=None):
        post = super(PostView, self).get_object(queryset)
        if not post.is_accessible_by(self.request.user):
            raise Http404()
        return post


class EditBlogPost(LoginRequiredMixin, TitleMixin, UpdateView):
    model = BlogPost
    pk_url_kwarg = "id"
    template_name = "blog/edit.html"
    form_class = BlogPostEditForm

    def get_title(self):
        return _("Edit %s") % self.object.title

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.object = self.get_object()
        if self.object.is_organization_private:
            return generic_message(
                request,
                _("Use organization edit"),
                _(
                    "This post is private to an organization. "
                    "Edit it through the organization page."
                ),
                status=400,
            )
        if not self.object.is_editable_by(request.user):
            return generic_message(
                request,
                _("Permission denied"),
                _("You are not allowed to edit this blog post."),
                status=403,
            )
        if request.profile.mute and not request.user.is_superuser:
            return generic_message(
                request,
                _("Muted"),
                _("Muted users are not allowed to edit blog posts."),
                status=403,
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["is_admin"] = self.request.user.is_superuser
        return kwargs

    def form_valid(self, form):
        # Slug must be regenerated before super().form_valid so that
        # get_success_url builds the redirect with the new slug.
        form.instance.slug = slugify(form.instance.title)[:50]
        with revisions.create_revision():
            res = super().form_valid(form)
            # Privacy is derived from org membership: a post is org-private
            # iff it has any organizations attached.
            new_state = self.object.organizations.exists()
            if self.object.is_organization_private != new_state:
                self.object.is_organization_private = new_state
                self.object.save()
            revisions.set_comment(_("Edited from site"))
            revisions.set_user(self.request.user)
        messages.success(self.request, _("Blog post updated."))
        return res

    def get_success_url(self):
        return reverse("blog_post", args=[self.object.id, self.object.slug])
