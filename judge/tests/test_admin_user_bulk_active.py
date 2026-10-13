"""A17 (#101): bulk deactivate / reactivate users from the admin.

Deactivation instead of deletion: the account cannot log in, its open
sessions are revoked, and its submissions, scores and comments are kept.
Hard deletion is A24 and stays disabled here.
"""

from unittest import mock

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import Permission, User
from django.contrib.sessions.models import Session
from django.test import TestCase, override_settings
from django.urls import reverse

from judge.models import Language, Profile
from judge.tasks.user import revoke_user_sessions

CHANGELIST = "admin:auth_user_changelist"


def make_user(username, password="pw", **kw):
    user = User.objects.create_user(username, f"{username}@x.com", password, **kw)
    Profile.objects.get_or_create(
        user=user, defaults={"language": Language.objects.first()}
    )
    return user


# English, so the asserted strings do not depend on whether the vi .mo is
# compiled (LANGUAGE_CODE is "vi").
@override_settings(LANGUAGE_CODE="en")
class BulkActiveActionTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.root = make_user("root", is_staff=True, is_superuser=True)
        base_perms = Permission.objects.filter(
            content_type__app_label="auth", codename__in=["view_user", "change_user"]
        )
        cls.limited = make_user("limited", is_staff=True)
        cls.limited.user_permissions.add(*base_perms)
        cls.deactivator = make_user("deactivator", is_staff=True)
        cls.deactivator.user_permissions.add(
            *base_perms, Permission.objects.get(codename="deactivate_user")
        )
        cls.other_staff = make_user("otherstaff", is_staff=True)
        cls.other_root = make_user("otherroot", is_staff=True, is_superuser=True)
        cls.students = [make_user(f"bulk_student{i}") for i in range(3)]
        cls.dormant = make_user("bulk_dormant", is_active=False)
        Profile.objects.filter(user__in=cls.students).update(points=123.0)

    # -- helpers --------------------------------------------------------------

    def post_action(self, actor, action, users, confirm=False, url_params="", **extra):
        self.client.force_login(actor)
        data = {
            "action": action,
            "index": 0,
            "_selected_action": [u.pk for u in users],
            **extra,
        }
        if confirm:
            data["post"] = "yes"
        return self.client.post(reverse(CHANGELIST) + url_params, data)

    def refresh(self, *users):
        return [User.objects.get(pk=u.pk) for u in users]

    # -- who sees the checkbox and the actions ---------------------------------

    def test_checkboxes_and_actions_only_for_permitted_accounts(self):
        for actor, visible in [
            (self.limited, False),
            (self.deactivator, True),
            (self.root, True),
        ]:
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                html = self.client.get(reverse(CHANGELIST)).content.decode()
                self.assertEqual('name="_selected_action"' in html, visible)
                self.assertEqual('value="deactivate_users"' in html, visible)
                self.assertEqual('value="activate_users"' in html, visible)
                self.assertNotIn('value="delete_selected"', html)  # deletion stays off

    def test_unpermitted_staff_cannot_post_the_action(self):
        self.post_action(
            self.limited, "deactivate_users", self.students[:1], confirm=True
        )
        self.assertTrue(self.refresh(self.students[0])[0].is_active)

    # -- confirmation ------------------------------------------------------------

    def test_confirmation_lists_targets_and_protected_accounts_and_changes_nothing(
        self,
    ):
        r = self.post_action(
            self.deactivator,
            "deactivate_users",
            self.students[:2]
            + [self.other_staff, self.other_root, self.deactivator, self.dormant],
        )
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("bulk_student0", html)
        self.assertIn("bulk_student1", html)
        self.assertIn("otherstaff: staff or superuser", html)
        self.assertIn("otherroot: staff or superuser", html)
        self.assertIn("deactivator: your own account", html)
        self.assertIn("1 selected account is already in that state", html)
        self.assertTrue(all(u.is_active for u in self.refresh(*self.students[:2])))

    # -- apply ---------------------------------------------------------------------

    @mock.patch("judge.tasks.user.revoke_user_sessions.delay")
    def test_deactivate_exact_users_with_audit_and_history_kept(self, delay):
        before = LogEntry.objects.count()
        r = self.post_action(
            self.deactivator,
            "deactivate_users",
            self.students[:2] + [self.other_staff, self.deactivator],
            confirm=True,
        )
        self.assertEqual(r.status_code, 302)
        s0, s1, s2 = self.refresh(*self.students)
        self.assertFalse(s0.is_active)
        self.assertFalse(s1.is_active)
        self.assertTrue(s2.is_active)  # not selected
        self.assertTrue(self.refresh(self.other_staff)[0].is_active)  # protected
        self.assertTrue(self.refresh(self.deactivator)[0].is_active)  # self
        # One audit row per changed account.
        logs = LogEntry.objects.order_by("id")[before:]
        self.assertEqual(
            sorted((int(e.object_id), e.action_flag, e.user_id) for e in logs),
            sorted((u.pk, CHANGE, self.deactivator.pk) for u in self.students[:2]),
        )
        # History is untouched: the rows and their data are still there.
        self.assertEqual(
            list(
                Profile.objects.filter(user__in=self.students[:2]).values_list(
                    "points", flat=True
                )
            ),
            [123.0, 123.0],
        )
        delay.assert_called_once_with([s0.pk, s1.pk])

    @mock.patch("judge.tasks.user.revoke_user_sessions.delay")
    def test_reactivate_is_symmetric(self, delay):
        self.post_action(self.root, "deactivate_users", self.students[:1], confirm=True)
        self.post_action(
            self.root,
            "activate_users",
            self.students[:1] + [self.dormant],
            confirm=True,
        )
        self.assertTrue(self.refresh(self.students[0])[0].is_active)
        self.assertTrue(self.refresh(self.dormant)[0].is_active)
        self.assertEqual(delay.call_count, 1)  # only deactivation revokes sessions

    def test_superusers_and_staff_are_protected_from_reactivation_too(self):
        User.objects.filter(pk=self.other_staff.pk).update(is_active=False)
        self.post_action(
            self.deactivator, "activate_users", [self.other_staff], confirm=True
        )
        self.assertFalse(self.refresh(self.other_staff)[0].is_active)

    @mock.patch("judge.tasks.user.revoke_user_sessions.delay")
    def test_select_all_matching_search_across_pages(self, delay):
        r = self.post_action(
            self.root,
            "deactivate_users",
            self.students[:1],
            url_params="?q=bulk_student",
            select_across="1",
        )
        html = r.content.decode()
        self.assertIn('name="select_across" value="1"', html)
        self.assertIn("This will deactivate 3 accounts", html)
        self.post_action(
            self.root,
            "deactivate_users",
            self.students[:1],
            confirm=True,
            url_params="?q=bulk_student",
            select_across="1",
        )
        self.assertFalse(any(u.is_active for u in self.refresh(*self.students)))
        self.assertTrue(self.refresh(self.dormant)[0].is_active is False)

    # -- login and sessions -----------------------------------------------------

    def test_deactivated_account_cannot_log_in(self):
        User.objects.filter(pk=self.students[0].pk).update(is_active=False)
        self.client.post(
            reverse("auth_login"), {"username": "bulk_student0", "password": "pw"}
        )
        self.assertNotIn(SESSION_KEY, self.client.session)
        # Control: an active account with the same setup logs in.
        self.client.post(
            reverse("auth_login"), {"username": "bulk_student1", "password": "pw"}
        )
        self.assertEqual(self.client.session.get(SESSION_KEY), str(self.students[1].pk))

    def test_revoke_task_deletes_only_the_given_users_sessions(self):
        from django.test import Client

        a, b = Client(), Client()
        a.force_login(self.students[0])
        b.force_login(self.students[1])
        key_a, key_b = a.session.session_key, b.session.session_key
        deleted = revoke_user_sessions([self.students[0].pk])
        self.assertEqual(deleted, 1)
        self.assertFalse(Session.objects.filter(session_key=key_a).exists())
        self.assertTrue(Session.objects.filter(session_key=key_b).exists())

    def test_open_session_is_dropped_and_does_not_revive_on_reactivation(self):
        # The case the task cannot cover if celery is down: the middleware
        # drops the stale login on the next request.
        self.client.force_login(self.students[2])
        User.objects.filter(pk=self.students[2].pk).update(is_active=False)
        self.client.get("/")
        self.assertNotIn(SESSION_KEY, self.client.session)
        User.objects.filter(pk=self.students[2].pk).update(is_active=True)
        self.client.get("/")
        self.assertNotIn(SESSION_KEY, self.client.session)
