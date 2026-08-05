"""The two-column home page layout.

The home page drops the left sidebar (groups / nav tabs) entirely and aligns
middle-right-content with the no-left-sidebar class. The right sidebar is
ordered: top users, ongoing contests, top contributors, comment stream. The
/blog/ list and organization pages keep their previous sidebar content.
"""

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import BlogPost, Comment, Language, Profile


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
        self.assertContains(response, 'class="middle-right-content no-left-sidebar"')

    def test_logged_in_home_has_no_groups_sidebar(self):
        profile = self._make_profile("member")
        self.client.force_login(profile.user)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Recent groups")
        self.assertNotContains(response, "home-sidebar-loggedin")
        self.assertContains(response, 'class="middle-right-content no-left-sidebar"')

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
