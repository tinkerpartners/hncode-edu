"""A05 (#95): discoverability / access / management matrix for groups and courses.

Decisions taken by the project owner (2026-10-13):
  1. Not publicly visible = hidden from lists and search AND refused at the URL
     for outsiders; members keep full access.
  2. "Admin" = superuser everywhere, group admins for their group, course
     Teacher/Assistant for their course; nobody beyond their scope.
  3. Organization.is_visible defaults to True, so existing groups are unchanged.

The full matrix is in docs/visibility-permissions.md; each test below is one
row of it.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import BlogPost, Course, CourseRole, Language, Organization, Profile
from judge.sitemap import OrganizationSitemap
from judge.views.contests import PrivateContestError

HIDDEN = "Hidden Group Zeta"
VISIBLE = "Visible Group Alpha"
HIDDEN_OPEN = "Hidden Open Group Omega"


def make_profile(username, **user_kwargs):
    user = User.objects.create_user(username, f"{username}@x.com", "pw", **user_kwargs)
    profile, _ = Profile.objects.get_or_create(
        user=user, defaults={"language": Language.objects.first()}
    )
    return profile


class OrganizationVisibilityTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.outsider = make_profile("outsider")
        cls.member = make_profile("member")
        cls.org_admin = make_profile("orgadmin")  # admin, deliberately not a member
        cls.superuser = make_profile("root", is_staff=True, is_superuser=True)
        cls.other_admin = make_profile("otheradmin")  # admin of a different group

        def org(name, slug, **kw):
            return Organization.objects.create(
                name=name,
                slug=slug,
                short_name=slug[:20],
                about="",
                registrant=cls.org_admin,
                **kw,
            )

        cls.visible = org(VISIBLE, "visible-alpha", is_open=False, is_visible=True)
        cls.hidden = org(
            HIDDEN,
            "hidden-zeta",
            is_open=False,
            is_visible=False,
            access_code="ZETA123",
        )
        cls.hidden_open = org(
            HIDDEN_OPEN, "hidden-omega", is_open=True, is_visible=False
        )
        for o in (cls.visible, cls.hidden, cls.hidden_open):
            o.admins.add(cls.org_admin)
            cls.member.organizations.add(o)
        cls.visible.admins.add(cls.other_admin)
        Organization.dirty_cache(cls.visible.id, cls.hidden.id, cls.hidden_open.id)
        Profile.get_admin_organization_ids.dirty(cls.org_admin)
        Profile.get_admin_organization_ids.dirty(cls.other_admin)

    def login(self, profile):
        if profile is not None:
            self.client.force_login(profile.user)

    def url(self, name, org, **kw):
        return reverse(name, args=(org.id, org.slug), **kw)

    # --- default --------------------------------------------------------------

    def test_new_groups_are_visible_by_default(self):
        o = Organization.objects.create(
            name="Default",
            slug="default-g",
            short_name="d",
            about="",
            registrant=self.outsider,
        )
        self.assertTrue(o.is_visible)

    # --- decision function ------------------------------------------------------

    def test_is_visible_to_matrix(self):
        cases = [
            (None, self.visible, True),
            (None, self.hidden, False),
            (self.outsider, self.visible, True),
            (self.outsider, self.hidden, False),
            (self.member, self.hidden, True),
            (self.org_admin, self.hidden, True),
            (self.other_admin, self.hidden, False),  # admin elsewhere only
            (self.superuser, self.hidden, True),
        ]
        for profile, org, expected in cases:
            with self.subTest(profile=profile and profile.user.username, org=org.slug):
                self.assertEqual(org.is_visible_to(profile), expected)

    def test_can_be_managed_by_matrix(self):
        cases = [
            (None, False),
            (self.outsider, False),
            (self.member, False),
            (self.other_admin, False),
            (self.org_admin, True),
            (self.superuser, True),
        ]
        for profile, expected in cases:
            with self.subTest(profile=profile and profile.user.username):
                self.assertEqual(self.hidden.can_be_managed_by(profile), expected)

    # --- list and search ----------------------------------------------------------

    def _list(self, profile, **params):
        self.login(profile)
        r = self.client.get(reverse("organization_list"), params)
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_list_all_tab(self):
        for profile, sees_hidden in [
            (None, False),
            (self.outsider, False),
            (self.other_admin, False),
            (self.member, True),
            (self.org_admin, True),
            (self.superuser, True),
        ]:
            with self.subTest(profile=profile and profile.user.username):
                self.client.logout()
                html = self._list(profile, tab="all")
                self.assertIn(VISIBLE, html)
                self.assertEqual(HIDDEN in html, sees_hidden)
                self.assertEqual(HIDDEN_OPEN in html, sees_hidden)

    def test_search_does_not_find_hidden_groups(self):
        self.assertNotIn(
            HIDDEN, self._list(self.outsider, tab="all", organization="Zeta")
        )
        self.client.logout()
        self.assertIn(HIDDEN, self._list(self.member, tab="all", organization="Zeta"))

    def test_public_tab_skips_hidden_open_group(self):
        self.assertNotIn(HIDDEN_OPEN, self._list(self.outsider, tab="public"))

    def test_sitemap_lists_only_visible_groups(self):
        ids = {i for i, _ in OrganizationSitemap().items()}
        self.assertIn(self.visible.id, ids)
        self.assertNotIn(self.hidden.id, ids)
        self.assertNotIn(self.hidden_open.id, ids)

    # --- direct URL ---------------------------------------------------------------

    def test_direct_urls_of_hidden_group(self):
        pages = ["organization_home", "organization_users", "organization_problems"]
        for name in pages:
            url = self.url(name, self.hidden)
            with self.subTest(page=name, who="anonymous"):
                self.client.logout()
                r = self.client.get(url)
                self.assertEqual(r.status_code, 302)
                self.assertIn("/accounts/login/", r["Location"])
            with self.subTest(page=name, who="outsider"):
                self.login(self.outsider)
                r = self.client.get(url)
                self.assertEqual(r.status_code, 403)
                self.assertNotIn(HIDDEN, r.content.decode())
            for who in (self.member, self.org_admin, self.superuser):
                with self.subTest(page=name, who=who.user.username):
                    self.client.logout()
                    self.login(who)
                    r = self.client.get(url)
                    self.assertEqual(r.status_code, 200)

    def test_wrong_slug_does_not_reveal_the_real_one(self):
        self.login(self.outsider)
        r = self.client.get(
            reverse("organization_home", args=(self.hidden.id, "guess"))
        )
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("hidden-zeta", r.get("Location", ""))

    def test_visible_group_home_is_still_open_to_outsiders(self):
        self.login(self.outsider)
        r = self.client.get(self.url("organization_home", self.visible))
        self.assertEqual(r.status_code, 200)
        self.assertIn(VISIBLE, r.content.decode())

    def test_outsider_visit_is_not_recorded_for_hidden_group(self):
        from judge.models import OrganizationProfile

        self.login(self.outsider)
        self.client.get(self.url("organization_home", self.hidden))
        self.assertFalse(
            OrganizationProfile.objects.filter(
                profile=self.outsider, organization=self.hidden
            ).exists()
        )

    def test_request_join_page_of_hidden_group(self):
        self.login(self.outsider)
        r = self.client.get(self.url("request_organization", self.hidden))
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(HIDDEN, r.content.decode())

    def test_invite_link_still_opens_a_hidden_group(self):
        self.login(self.outsider)
        url = self.url("join_organization", self.hidden)
        r = self.client.get(url, {"code": "ZETA123"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(HIDDEN, r.content.decode())
        r = self.client.post(url, {"code": "ZETA123"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self.outsider.organizations.filter(id=self.hidden.id).exists())

    def test_wrong_invite_code_is_refused_like_a_missing_group(self):
        self.login(self.outsider)
        r = self.client.get(
            self.url("join_organization", self.hidden), {"code": "NOPE000"}
        )
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(HIDDEN, r.content.decode())

    def test_group_hidden_after_being_blocked_stays_unblockable(self):
        from judge.models import Block

        Block.add_block(self.outsider, self.hidden)
        try:
            html = self._list(self.outsider, tab="blocked")
            self.assertIn(HIDDEN, html)
            self.assertNotIn(HIDDEN, self._list(self.outsider, tab="all"))
            r = self.client.post(self.url("unblock_organization", self.hidden))
            self.assertEqual(r.status_code, 302)
            self.assertFalse(Block.is_blocked(self.outsider, self.hidden))
            # Unblocking does not open the group's pages.
            r = self.client.get(self.url("organization_home", self.hidden))
            self.assertEqual(r.status_code, 403)
        finally:
            if Block.is_blocked(self.outsider, self.hidden):
                Block.remove_block(self.outsider, self.hidden)

    # --- management ---------------------------------------------------------------

    def test_edit_page_of_hidden_group(self):
        url = self.url("edit_organization", self.hidden)
        for profile, ok in [
            (self.org_admin, True),
            (self.superuser, True),
            (self.member, False),
            (self.other_admin, False),
        ]:
            with self.subTest(profile=profile.user.username):
                self.client.logout()
                self.login(profile)
                r = self.client.get(url)
                self.assertEqual(r.status_code == 200, ok, r.status_code)
                if ok:
                    self.assertContains(r, 'name="is_visible"')

    def test_toggling_visibility_takes_effect_immediately(self):
        url = self.url("organization_home", self.visible)
        self.login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 200)
        Organization.objects.filter(id=self.visible.id).update(is_visible=False)
        self.assertEqual(self.client.get(url).status_code, 403)
        Organization.objects.filter(id=self.visible.id).update(is_visible=True)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_hiding_a_group_keeps_its_admin_and_members(self):
        Organization.objects.filter(id=self.visible.id).update(is_visible=False)
        for profile in (self.member, self.other_admin):
            with self.subTest(profile=profile.user.username):
                self.client.logout()
                self.login(profile)
                r = self.client.get(self.url("organization_home", self.visible))
                self.assertEqual(r.status_code, 200)

    # --- other exposures ----------------------------------------------------------

    def test_member_autocomplete_of_hidden_group(self):
        url = reverse("profile_select2")
        self.login(self.outsider)
        self.assertEqual(
            self.client.get(url, {"org_id": self.hidden.id, "term": "m"}).status_code,
            404,
        )
        self.client.logout()
        self.login(self.org_admin)
        r = self.client.get(url, {"org_id": self.hidden.id, "term": "mem"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("member", r.content.decode())

    def test_private_contest_error_names_only_visible_groups(self):
        err = PrivateContestError(
            "c",
            False,
            True,
            Organization.objects.filter(id__in=[self.visible.id, self.hidden.id]),
        )
        self.assertEqual(
            set(err.visible_orgs(self.outsider).values_list("id", flat=True)),
            {self.visible.id},
        )
        self.assertEqual(
            set(err.visible_orgs(self.member).values_list("id", flat=True)),
            {self.visible.id, self.hidden.id},
        )

    def test_post_of_hidden_open_group_is_not_public(self):
        post = BlogPost.objects.create(
            title="t",
            content="c",
            visible=True,
            is_organization_private=True,
            publish_on=timezone.now() - timedelta(days=1),
        )
        post.organizations.add(self.hidden_open)
        self.assertFalse(post.is_accessible_by(self.outsider.user))
        self.assertTrue(post.is_accessible_by(self.member.user))


class CourseVisibilityTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.outsider = make_profile("c_outsider")
        cls.org_member = make_profile("c_orgmember")  # in the group, not enrolled
        cls.student = make_profile("c_student")
        cls.teacher = make_profile("c_teacher")
        cls.assistant = make_profile("c_assistant")
        cls.superuser = make_profile("c_root", is_staff=True, is_superuser=True)
        cls.other_teacher = make_profile("c_otherteacher")

        cls.group = Organization.objects.create(
            name="Course Group",
            slug="course-group",
            short_name="cg",
            about="",
            registrant=cls.teacher,
        )
        cls.org_member.organizations.add(cls.group)

        def course(name, slug, is_public, org=None):
            c = Course.objects.create(
                name=name,
                slug=slug,
                about=f"About {name}",
                is_public=is_public,
                is_open=True,
            )
            if org:
                c.organizations.add(org)
            for p, role in [
                (cls.student, "ST"),
                (cls.teacher, "TE"),
                (cls.assistant, "AS"),
            ]:
                CourseRole.objects.create(course=c, user=p, role=role)
            return c

        cls.public = course("Public Course Kappa", "public-kappa", True)
        cls.private = course("Private Course Sigma", "private-sigma", False)
        cls.group_course = course(
            "Group Course Lambda", "group-lambda", True, cls.group
        )
        cls.elsewhere = Course.objects.create(
            name="Other Course",
            slug="other-course",
            about="x",
            is_public=False,
            is_open=False,
        )
        CourseRole.objects.create(
            course=cls.elsewhere, user=cls.other_teacher, role="TE"
        )

    def login(self, profile):
        self.client.logout()
        if profile is not None:
            self.client.force_login(profile.user)

    def test_is_visible_to_matrix(self):
        cases = [
            (None, self.public, True),
            (None, self.private, False),
            (None, self.group_course, False),
            (self.outsider, self.private, False),
            (self.outsider, self.group_course, False),
            (self.org_member, self.group_course, True),
            (self.org_member, self.private, False),
            (self.student, self.private, True),
            (self.teacher, self.private, True),
            (self.assistant, self.private, True),
            (self.other_teacher, self.private, False),
            (self.superuser, self.private, True),
        ]
        for profile, course, expected in cases:
            with self.subTest(
                profile=profile and profile.user.username, course=course.slug
            ):
                self.assertEqual(course.is_visible_to(profile), expected)

    def test_all_tab_lists_only_visible_courses(self):
        names = {
            self.public: "Public Course Kappa",
            self.private: "Private Course Sigma",
            self.group_course: "Group Course Lambda",
        }
        for profile in (
            None,
            self.outsider,
            self.org_member,
            self.student,
            self.superuser,
        ):
            with self.subTest(profile=profile and profile.user.username):
                self.login(profile)
                html = self.client.get(
                    reverse("course_list"), {"tab": "all"}
                ).content.decode()
                for course, name in names.items():
                    self.assertEqual(name in html, course.is_visible_to(profile), name)

    def test_detail_url(self):
        # (profile, course, expected status, page shows the course name)
        cases = [
            (None, self.public, 200, True),  # enrolment page
            (None, self.private, 302, False),  # login
            (self.outsider, self.public, 200, True),
            (self.outsider, self.private, 404, False),
            (self.outsider, self.group_course, 404, False),
            (self.org_member, self.group_course, 200, True),
            (self.student, self.private, 200, True),
            (self.teacher, self.private, 200, True),
            (self.superuser, self.private, 200, True),
        ]
        for profile, course, status, shows_name in cases:
            with self.subTest(
                profile=profile and profile.user.username, course=course.slug
            ):
                self.login(profile)
                r = self.client.get(reverse("course_detail", args=(course.slug,)))
                self.assertEqual(r.status_code, status)
                self.assertEqual(course.name in r.content.decode(), shows_name)

    def test_management_stays_inside_the_course(self):
        for profile, course, ok in [
            (self.teacher, self.private, True),
            (self.assistant, self.private, True),
            (self.superuser, self.private, True),
            (self.student, self.private, False),
            (self.outsider, self.private, False),
            (self.other_teacher, self.private, False),  # teacher of another course
            (self.teacher, self.elsewhere, False),
        ]:
            with self.subTest(profile=profile.user.username, course=course.slug):
                self.login(profile)
                r = self.client.get(reverse("course_edit", args=(course.slug,)))
                self.assertEqual(r.status_code == 200, ok, r.status_code)

    def test_grades_need_enrolment(self):
        for profile, ok in [
            (self.student, True),
            (self.teacher, True),
            (self.org_member, False),
            (self.outsider, False),
        ]:
            with self.subTest(profile=profile.user.username):
                self.login(profile)
                r = self.client.get(
                    reverse("course_grades", args=(self.group_course.slug,))
                )
                self.assertEqual(r.status_code == 200, ok, r.status_code)

    def test_making_a_course_private_keeps_enrolled_users(self):
        Course.objects.filter(id=self.public.id).update(is_public=False)
        self.login(self.student)
        r = self.client.get(reverse("course_detail", args=(self.public.slug,)))
        self.assertEqual(r.status_code, 200)
        self.login(self.outsider)
        r = self.client.get(reverse("course_detail", args=(self.public.slug,)))
        self.assertEqual(r.status_code, 404)
