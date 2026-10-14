"""A10: the action bar offers Like only and shows the like count."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import BlogPost, Language, Profile
from judge.models.pagevote import PageVoteVoter, VoteService


class LikeOnlyVoteTest(TestCase):
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

    def _user(self, name, staff=False):
        user = User.objects.create_user(username=name, password="pw", is_staff=staff)
        Profile.objects.get_or_create(user=user, defaults={"language": self.language})
        return user

    def setUp(self):
        self.author = self._user("a10author")
        # Staff skip the "solve a problem before voting" rule.
        self.voter = self._user("a10voter", staff=True)
        self.blog = BlogPost.objects.create(
            title="A10",
            slug="a10",
            visible=True,
            publish_on=timezone.now(),
            content="x",
        )
        self.blog.authors.add(self.author.profile)
        self.pagevote = self.blog.get_or_create_pagevote()
        self.client.force_login(self.voter)

    def _vote(self, delta):
        return self.client.post(
            reverse("pagevote_vote"),
            {"id": self.pagevote.id, "delta": delta},
            SERVER_NAME="localhost",
        )

    def test_downvote_is_refused_and_not_stored(self):
        response = self._vote(-1)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PageVoteVoter.objects.filter(pagevote=self.pagevote).exists())
        self.pagevote.refresh_from_db()
        self.assertEqual(self.pagevote.score, 0)

    def test_like_and_unlike_return_like_count(self):
        response = self._vote(1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["like_count"], 1)
        response = self._vote(0)
        self.assertEqual(response.json()["like_count"], 0)

    def test_like_count_ignores_old_dislikes(self):
        # Old downvotes stay in the data and in the net score.
        for name in ("old1", "old2"):
            VoteService.vote(self.blog, self._user(name, staff=True), -1)
        response = self._vote(1)
        self.assertEqual(response.json()["like_count"], 1)
        self.assertEqual(response.json()["current_score"], -1)
        self.assertEqual(PageVoteVoter.objects.filter(score=-1).count(), 2)

    def test_old_dislike_is_replaced_by_like(self):
        VoteService.vote(self.blog, self.voter, -1)
        response = self._vote(1)
        self.assertEqual(response.json()["like_count"], 1)
        self.assertEqual(
            PageVoteVoter.objects.get(
                pagevote=self.pagevote, voter=self.voter.profile
            ).score,
            1,
        )

    def test_page_shows_like_count_without_dislike_button(self):
        VoteService.vote(self.blog, self._user("liker", staff=True), 1)
        VoteService.vote(self.blog, self._user("hater", staff=True), -1)
        html = self.client.get(
            reverse("blog_post", args=(self.blog.id, self.blog.slug)),
            SERVER_NAME="localhost",
        ).content.decode()
        self.assertIn('id="like-button-%d"' % self.pagevote.id, html)
        self.assertNotIn("dislike-button", html)
        self.assertNotIn("pagevote_downvote", html)
        # 1 like and 1 dislike: the like count is 1 (the net score would be 0).
        self.assertRegex(html, r'id="pagevote-score-%d">\s*1\s*<' % self.pagevote.id)
