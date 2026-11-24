from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import BlogPost, Organization, Profile
from judge.tests.strict_helpers import make_language


class OrganizationBlogDateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        language = make_language()
        cls.profiles = {}
        for name, superuser in (("date_admin", True), ("date_member", False)):
            user = User.objects.create_user(
                name, password="pass", is_superuser=superuser
            )
            cls.profiles[name], _ = Profile.objects.get_or_create(
                user=user, defaults={"language": language}
            )
        cls.organization = Organization.objects.create(
            name="Date Org",
            slug="date-org",
            short_name="DO",
            about="x",
            registrant=cls.profiles["date_admin"],
            is_open=True,
        )
        cls.organization.admins.add(cls.profiles["date_admin"])
        cls.organization.members.add(*cls.profiles.values())
        cls.published = timezone.now() - timedelta(days=30)

    def make_post(self, **fields):
        post = BlogPost.objects.create(
            title="Post",
            slug="post",
            content="Body",
            publish_on=self.published,
            is_organization_private=True,
            **fields,
        )
        post.authors.add(self.profiles["date_member"])
        post.organizations.add(self.organization)
        return post

    def edit(self, post, name, **data):
        self.client.force_login(self.profiles[name].user)
        response = self.client.post(
            reverse(
                "edit_organization_blog",
                args=[self.organization.pk, self.organization.slug, post.pk],
            ),
            {
                "title": "Edited",
                "content": "Body",
                "organizations": [self.organization.pk],
                # A submitted date must be ignored.
                "publish_on": "2030-01-01 00:00:00",
                **data,
            },
        )
        self.assertEqual(response.status_code, 302)
        post.refresh_from_db()
        self.assertEqual(post.title, "Edited")

    def test_editing_keeps_publish_date(self):
        visible = self.make_post(visible=True)
        self.edit(visible, "date_admin", visible="on")
        self.assertEqual(visible.publish_on, self.published)
        pending = self.make_post(visible=False)
        self.edit(pending, "date_member")
        self.assertEqual(pending.publish_on, self.published)

    def test_new_post_is_dated_now(self):
        self.client.force_login(self.profiles["date_admin"].user)
        before = timezone.now() - timedelta(minutes=1)
        response = self.client.post(
            reverse(
                "add_organization_blog",
                args=[self.organization.pk, self.organization.slug],
            ),
            {
                "title": "New post",
                "content": "Body",
                "visible": "on",
                "organizations": [self.organization.pk],
                "publish_on": "2030-01-01 00:00:00",
            },
        )
        self.assertEqual(response.status_code, 302)
        post = BlogPost.objects.get(title="New post")
        self.assertTrue(
            before <= post.publish_on <= timezone.now() + timedelta(minutes=1)
        )
