"""Admin changelist for Organization: every page must hold rows (issue #97, A02).

On hncode.edu.vn ~700 of ~2,200 organizations have a ``registrant_id`` whose
Profile row was deleted outside the ORM (the FK constraint exists; the rows do
not). Django's admin ``select_related()``'d that non-nullable FK, i.e. an
INNER JOIN, so those rows fell out of every page slice while ``paginator.count``
(no join) still counted them: pages 1-14 full, page 15 short, pages 16-22 empty.

These tests build the same shape (ghost registrants) and check that the page
slices, the count, the ordering and the out-of-range handling all agree, for a
superuser and for an organization admin with restricted permissions.
"""

import math
import re
from unittest import skipUnless

from django.contrib.auth.models import Permission, User
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from judge.admin.organization import OrganizationAdmin
from judge.models import Language, Organization, Profile

PAGE = OrganizationAdmin.list_per_page
TOTAL = 18 * PAGE + 50  # 1850 organizations -> 19 pages, last one half full
LIMITED_TOTAL = 2 * PAGE + 50  # 250 -> 3 pages for the restricted admin

ROW_RE = re.compile(r'class="field-name"')
# The change link is present for every row and every permission level; the
# action checkbox is not (no delete permission -> no actions -> no checkbox).
PK_RE = re.compile(r"/judge/organization/(\d+)/change/")


@skipUnless(
    connection.vendor == "mysql",
    "ghost registrants need FK checks switched off inside a transaction",
)
class OrganizationChangelistPaginationTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        lang = Language.objects.first()

        def make_profile(username, **user_kwargs):
            user = User.objects.create_user(
                username, f"{username}@x.com", "pw", **user_kwargs
            )
            profile, _ = Profile.objects.get_or_create(
                user=user, defaults={"language": lang}
            )
            return profile

        cls.superuser = make_profile("root", is_staff=True, is_superuser=True)
        cls.limited = make_profile("orgadmin", is_staff=True)
        cls.limited.user.user_permissions.add(
            Permission.objects.get(codename="change_organization"),
            Permission.objects.get(codename="view_organization"),
        )
        cls.alive = make_profile("alive")
        ghost = make_profile("ghost")

        # Alternate live/ghost registrants so every page mixes both kinds.
        Organization.objects.bulk_create(
            [
                Organization(
                    name=f"Org {i:04d}",
                    slug=f"org-{i:04d}",
                    short_name=f"O{i}",
                    about="",
                    registrant=ghost if i % 2 else cls.alive,
                )
                for i in range(TOTAL)
            ]
        )
        cls.ghost_count = sum(1 for i in range(TOTAL) if i % 2)

        # The restricted admin administers the first LIMITED_TOTAL organizations.
        limited_ids = list(
            Organization.objects.order_by("id").values_list("id", flat=True)[
                :LIMITED_TOTAL
            ]
        )
        through = Organization.admins.through
        through.objects.bulk_create(
            [
                through(organization_id=oid, profile_id=cls.limited.id)
                for oid in limited_ids
            ]
        )

        # Delete the ghost Profile the way production lost them: straight SQL,
        # constraint checks off, leaving dangling registrant_id values behind.
        with connection.constraint_checks_disabled():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM judge_profile WHERE id = %s", [ghost.id])
        cls.ghost_id = ghost.id

    def setUp(self):
        self.url = reverse("admin:judge_organization_changelist")

    # -- helpers -----------------------------------------------------------

    def _page(self, page, **params):
        params["p"] = page
        return self.client.get(self.url, params)

    @staticmethod
    def _rows(response):
        return len(ROW_RE.findall(response.content.decode()))

    @staticmethod
    def _ids(response):
        return PK_RE.findall(response.content.decode())

    def _assert_pages_cover(self, total, **params):
        pages = math.ceil(total / PAGE)
        self.assertGreaterEqual(pages, 3)
        seen = []
        for page in range(1, pages + 1):
            response = self._page(page, **params)
            self.assertEqual(response.status_code, 200, page)
            expected = PAGE if page < pages else total - PAGE * (pages - 1)
            self.assertEqual(self._rows(response), expected, f"page {page}")
            seen.extend(self._ids(response))
        self.assertEqual(len(seen), total)
        self.assertEqual(len(set(seen)), total, "a row appeared on two pages")
        # One past the end: Django's changelist redirects rather than 500ing
        # or rendering an empty table.
        response = self._page(pages + 1, **params)
        self.assertEqual(response.status_code, 302)
        self.assertIn("e=1", response["Location"])
        return pages

    # -- tests -------------------------------------------------------------

    def test_fixture_has_the_production_shape(self):
        self.assertEqual(Organization.objects.count(), TOTAL)
        self.assertGreaterEqual(math.ceil(TOTAL / PAGE), 18)
        self.assertFalse(Profile.objects.filter(id=self.ghost_id).exists())
        self.assertEqual(
            Organization.objects.filter(registrant_id=self.ghost_id).count(),
            self.ghost_count,
        )

    def test_superuser_sees_every_organization_across_all_pages(self):
        self.client.force_login(self.superuser.user)
        pages = self._assert_pages_cover(TOTAL)
        self.assertEqual(pages, 19)
        # The reported total matches what was paginated.
        response = self._page(1)
        self.assertContains(response, f"{TOTAL} ")

    def test_ghost_registrant_renders_as_empty_not_missing_row(self):
        self.client.force_login(self.superuser.user)
        response = self._page(1)
        html = response.content.decode()
        self.assertEqual(self._rows(response), PAGE)
        # Live registrants show their username; ghosts fall back to the
        # admin's empty value display instead of dropping the row.
        self.assertEqual(html.count('class="field-registrant_name">alive<'), PAGE // 2)
        self.assertEqual(html.count('class="field-registrant_name">-<'), PAGE // 2)

    def test_ordering_is_stable_between_loads(self):
        self.client.force_login(self.superuser.user)
        first = self._ids(self._page(10))
        second = self._ids(self._page(10))
        self.assertEqual(len(first), PAGE)
        self.assertEqual(first, second)
        # Sorting by the registrant column (an annotation) must not 500.
        response = self._page(1, o="6")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._rows(response), PAGE)

    def test_registrant_state_filter_partitions_the_list(self):
        self.client.force_login(self.superuser.user)
        deleted = self._assert_pages_cover(self.ghost_count, registrant_state="deleted")
        exists = self._assert_pages_cover(
            TOTAL - self.ghost_count, registrant_state="exists"
        )
        self.assertGreaterEqual(deleted, 3)
        self.assertGreaterEqual(exists, 3)

    def test_search_by_registrant_username_still_works(self):
        self.client.force_login(self.superuser.user)
        response = self._page(1, q="alive")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._rows(response), PAGE)
        self.assertContains(response, f"{TOTAL - self.ghost_count} ")

    def test_limited_admin_pages_match_their_own_organizations(self):
        self.client.force_login(self.limited.user)
        self.assertFalse(self.limited.user.has_perm("judge.edit_all_organization"))
        pages = self._assert_pages_cover(LIMITED_TOTAL)
        self.assertEqual(pages, 3)
        response = self._page(1)
        self.assertContains(response, f"{LIMITED_TOTAL} ")


class OrganizationRegistrantSetNullTest(TestCase):
    """Deleting a user keeps the organizations they registered (#97 follow-up).

    ``registrant`` used to be ``on_delete=CASCADE``: an ORM delete of one user
    removed every organization they had registered, and with it the links to
    every contest and problem in those organizations.
    """

    fixtures = ["language_small"]

    def setUp(self):
        lang = Language.objects.first()
        user = User.objects.create_user("founder", "f@x.com", "pw")
        self.founder, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": lang}
        )
        root = User.objects.create_user(
            "root", "r@x.com", "pw", is_staff=True, is_superuser=True
        )
        Profile.objects.get_or_create(user=root, defaults={"language": lang})
        self.root = root
        self.org = Organization.objects.create(
            name="Kept", slug="kept", short_name="K", about="", registrant=self.founder
        )

    def test_deleting_the_registrant_keeps_the_organization(self):
        self.founder.user.delete()
        self.org.refresh_from_db()
        self.assertIsNone(self.org.registrant_id)

    def test_registrantless_organization_is_listed_and_filtered_as_deleted(self):
        self.founder.user.delete()
        self.client.force_login(self.root)
        url = reverse("admin:judge_organization_changelist")
        response = self.client.get(url, {"registrant_state": "deleted"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(ROW_RE.findall(response.content.decode())), 1)
        response = self.client.get(url, {"registrant_state": "exists"})
        self.assertEqual(len(ROW_RE.findall(response.content.decode())), 0)
        response = self.client.get(
            reverse("admin:judge_organization_change", args=(self.org.id,))
        )
        self.assertEqual(response.status_code, 200)
