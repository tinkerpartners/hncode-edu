"""The two-column home page layout.

The home page drops the left sidebar (groups / nav tabs) entirely and aligns
middle-right-content with the no-left-sidebar class. The home sidebar provides
profile, contest and ranking summaries alongside the comment stream.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    BlogPost,
    Comment,
    Contest,
    Course,
    CourseRole,
    Language,
    Organization,
    OrganizationProfile,
    Problem,
    ProblemGroup,
    Profile,
)
from judge.models.course import CourseVisit


class HomeLayoutTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.language, _ = Language.objects.get_or_create(
            key="PY3",
            defaults={
                "name": "Python 3",
                "short_name": "PY3",
                "common_name": "Python",
                "ace": "python",
                "pygments": "python3",
                "template": "",
            },
        )

    def setUp(self):
        cache.clear()

    def _make_profile(self, name, rating=None, contribution_points=None):
        user = User.objects.create_user(username=name, password="password123")
        profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": self.language}
        )
        if rating is not None:
            profile.rating = rating
        if contribution_points is not None:
            profile.contribution_points = contribution_points
        if rating is not None or contribution_points is not None:
            profile.save()
        return profile

    def _make_comment(self, author):
        post = BlogPost.objects.create(
            title="A post to comment on",
            slug="a-post",
            content="Content",
            publish_on=timezone.now(),
            visible=True,
        )
        return Comment.objects.create(
            content_type=ContentType.objects.get_for_model(BlogPost),
            object_id=post.id,
            author=author,
            body="A recent comment",
        )

    def test_logged_out_home_has_no_left_sidebar(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "left-sidebar-item")
        self.assertNotContains(response, 'id="home-profile-title"')
        self.assertNotContains(response, 'id="home-recent-title"')
        self.assertNotContains(response, "home-news-header")
        self.assertContains(response, 'class="middle-right-content no-left-sidebar"')

    def test_logged_in_home_has_profile_and_recent_learning_in_right_sidebar(self):
        profile = self._make_profile("member")
        self.client.force_login(profile.user)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "home-sidebar-loggedin")
        self.assertContains(response, 'class="middle-right-content no-left-sidebar"')
        self.assertContains(response, 'id="home-profile-title"')
        self.assertContains(response, reverse("user_problems", args=["member"]))
        content = response.content.decode()
        recent = content.index('id="home-recent-title"')
        self.assertLess(content.index('id="home-profile-title"'), recent)
        self.assertLess(recent, content.index('id="home-contests-title"'))

    def test_course_visits_are_recorded_and_rechecked_for_access(self):
        profile = self._make_profile("learner")
        self.client.force_login(profile.user)
        courses = [
            Course.objects.create(name=f"Test course {slug}", slug=slug)
            for slug in ("first", "second", "unavailable")
        ]
        first, second, unavailable = courses
        first_role = CourseRole.objects.create(user=profile, course=first)
        CourseRole.objects.create(user=profile, course=second)
        self.assertEqual(
            self.client.get(unavailable.get_absolute_url()).status_code, 404
        )
        for course in (first, second, first):
            self.assertEqual(
                self.client.get(course.get_absolute_url()).status_code, 200
            )
        self.assertEqual(CourseVisit.objects.filter(profile=profile).count(), 2)
        response = self.client.get(reverse("home"))
        self.assertEqual(
            list(response.context_data["home_recent_courses"]), [first, second]
        )

        first_role.delete()
        response = self.client.get(reverse("home"))
        self.assertEqual(list(response.context_data["home_recent_courses"]), [second])
        self.client.force_login(self._make_profile("other_learner").user)
        self.assertFalse(
            self.client.get(reverse("home")).context_data["home_recent_courses"]
        )

    def test_recent_groups_follow_current_visibility_and_account(self):
        profile = self._make_profile("recent_groups_owner")
        other = self._make_profile("recent_groups_other")
        public = Organization.objects.create(
            name="Open recent group", slug="open-recent"
        )
        hidden = Organization.objects.create(
            name="Hidden recent group", slug="hidden-recent", is_visible=False
        )
        OrganizationProfile.add_organization(profile, public)
        OrganizationProfile.add_organization(profile, hidden)
        OrganizationProfile.add_organization(other, hidden)
        profile.organizations.add(hidden)
        self.client.force_login(profile.user)
        response = self.client.get(reverse("home"))
        self.assertEqual(
            [group["pk"] for group in response.context_data["home_recent_groups"]],
            [hidden.pk, public.pk],
        )
        self.client.force_login(other.user)
        self.assertFalse(
            self.client.get(reverse("home")).context_data["home_recent_groups"]
        )

    def test_contest_tabs_respect_visibility_and_status(self):
        now = timezone.now()
        for key, start, end, private in [
            ("home_past", -4, -2, False),
            ("home_current", -1, 1, False),
            ("home_future", 2, 4, False),
            ("home_private", -5, -1, True),
        ]:
            Contest.objects.create(
                key=key,
                name=key,
                start_time=now + timedelta(hours=start),
                end_time=now + timedelta(hours=end),
                is_visible=True,
                is_private=private,
            )
        response = self.client.get(reverse("home"))
        for tab, key in [
            ("past", "home_past"),
            ("current", "home_current"),
            ("future", "home_future"),
        ]:
            self.assertEqual(
                [contest.key for contest in response.context_data[f"{tab}_contests"]],
                [key],
            )
            self.assertContains(response, f"?tab={tab}")
        self.assertNotContains(response, "home_private")

    def test_guest_pagination_keeps_home_news_cards(self):
        now = timezone.now()
        for index in range(5):
            BlogPost.objects.create(
                title=f"News page {index}",
                slug=f"news-page-{index}",
                content="Content",
                publish_on=now - timedelta(hours=index),
                visible=True,
            )
        response = self.client.get(reverse("home"), {"only_content": 1, "page": 2})
        self.assertContains(response, "home-news-card")
        self.assertContains(response, "News page 4")
        self.assertNotContains(response, "News page 0")
        self.assertNotContains(response, "home-dashboard-sidebar")

    def test_post_pages_only_show_published_posts_visible_to_the_member(self):
        member = self._make_profile("blog_member")
        group = Organization.objects.create(name="Member group", slug="member-group")
        other = Organization.objects.create(name="Other group", slug="other-group")
        member.organizations.add(group)
        now = timezone.now()
        for i in range(5):
            post = BlogPost.objects.create(
                title=f"Article {i}",
                slug=f"article-{i}",
                content="Full article only",
                summary="Short summary",
                publish_on=now - timedelta(minutes=i + 1),
                visible=True,
                is_organization_private=i == 4,
            )
            if i == 4:
                post.organizations.add(group)
        private = BlogPost.objects.create(
            title="Other group article",
            slug="other-group-article",
            content="Private",
            publish_on=now,
            visible=True,
            is_organization_private=True,
        )
        private.organizations.add(other)
        self.client.force_login(member.user)
        with patch("judge.views.blog.build_home_feed") as build:
            for route in ("home", "blog_post_list"):
                first = self.client.get(reverse(route))
                self.assertContains(first, "Full article only")
                self.assertNotContains(first, "Short summary")
                self.assertContains(first, "data-article-toggle", count=4)
                self.assertNotContains(first, private.title)
                fragment = self.client.get(
                    reverse(route), {"only_content": 1, "page": 2}
                )
                self.assertContains(fragment, "Article 4")
                self.assertContains(fragment, "data-article-toggle", count=1)
                self.assertNotContains(fragment, "Article 0")
            build.assert_not_called()

    def test_suggestions_tab_uses_the_non_blog_feed(self):
        member = self._make_profile("suggestion_member")
        course = Course.objects.create(name="Suggested course", slug="suggested-course")
        CourseRole.objects.create(user=member, course=course)
        problem = Problem.objects.create(
            code="suggested",
            name="Suggested exercise",
            description="A sample exercise statement",
            group=ProblemGroup.objects.create(name="Feed", full_name="Feed problems"),
            is_public=True,
            time_limit=1,
            memory_limit=262144,
            points=100,
        )
        contest = Contest.objects.create(
            key="suggested_contest",
            name="Suggested contest",
            description="A sample contest description",
            start_time=timezone.now() - timedelta(hours=1),
            end_time=timezone.now() + timedelta(hours=1),
            is_visible=True,
        )
        comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(Problem),
            object_id=problem.id,
            author=member,
            body="A recent comment on the suggested exercise",
        )
        BlogPost.objects.create(
            title="Blog article only",
            slug="blog-article-only",
            content="Content",
            publish_on=timezone.now(),
            visible=True,
        )
        url = reverse("home")
        self.assertContains(self.client.get(url, {"tab": "suggestions"}), "next=")
        self.client.force_login(member.user)
        with (
            patch("judge.utils.problems.hot_problems", return_value=[problem]),
            patch(
                "judge.utils.contest_recommendation.get_recommended_contests",
                return_value=[contest.id],
            ),
        ):
            cursor = None
            for params in (
                {"tab": "suggestions"},
                {"tab": "suggestions", "only_content": 1},
            ):
                response = self.client.get(url, params)
                if "only_content" not in params:
                    cursor = response.context_data["next_cursor"]
                self.assertContains(response, problem.get_absolute_url())
                for preview_id in (
                    f"problem-preview-{problem.id}",
                    f"contest-preview-{contest.id}",
                    f"comment-preview-{comment.id}",
                ):
                    self.assertContains(response, f'id="{preview_id}"', count=1)
                    self.assertContains(
                        response, f'aria-controls="{preview_id}"', count=1
                    )
                self.assertNotContains(response, "Blog article only")
            self.assertTrue(cursor)
            more = self.client.get(
                url, {"tab": "suggestions", "only_content": 1, "cursor": cursor}
            )
            self.assertContains(more, course.name)
            self.assertContains(more, 'class="home-news-card feed-inline-card"')

    def test_comment_stream_shows_recent_comments(self):
        author = self._make_profile("author")
        self._make_comment(author)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "comment-stream")
        self.assertContains(response, "A post to comment on")

    def test_right_sidebar_order_top_users_before_comment_stream(self):
        author = self._make_profile("rated", rating=2000)
        self._make_comment(author)
        response = self.client.get(reverse("home"))
        content = response.content.decode()
        # Translation-proof markers: the Top Rating "view all" link and the
        # comment-stream box class.
        top_users = content.index("?order=-rating")
        stream = content.index("comment-stream")
        self.assertLess(top_users, stream)

    def test_blog_list_keeps_its_sidebar_and_gets_no_comment_stream(self):
        author = self._make_profile("rated2", rating=2000, contribution_points=50)
        self._make_comment(author)
        response = self.client.get(reverse("blog_post_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "?order=-rating")
        self.assertContains(response, "?order=-contribution_points")
        self.assertNotContains(response, "comment-stream")
