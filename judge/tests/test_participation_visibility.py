from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from judge.forms import EditOrganizationForm
from judge.models import (
    Contest,
    Course,
    CourseRole,
    Organization,
    Problem,
    ProblemGroup,
)
from judge.models.course import CourseJoinRequest, RoleInCourse
from judge.sitemap import OrganizationSitemap
from judge.tests.test_visibility_permissions import make_profile


class ParticipationVisibilityTests(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.manager = make_profile("access_manager")
        cls.student = make_profile("access_student")
        cls.applicant = make_profile("access_applicant")
        cls.outsider = make_profile("access_outside")
        cls.group = Organization.objects.create(
            name="Nhóm duyệt QA",
            slug="qa-approval-group",
            short_name="QA",
            about="Nhóm mẫu",
            registrant=cls.manager,
            is_open=False,
            access_code="JOINGRP",
        )
        cls.group.admins.add(cls.manager)
        cls.group.members.add(cls.manager, cls.student, cls.applicant)
        cls.course = Course.objects.create(
            name="Khóa duyệt QA",
            slug="qa-approval-course",
            is_public=True,
            is_open=False,
            invite_token="a" * 43,
        )
        cls.course.organizations.add(cls.group)
        for profile, role in (
            (cls.manager, RoleInCourse.TEACHER),
            (cls.student, RoleInCourse.STUDENT),
        ):
            CourseRole.objects.create(course=cls.course, user=profile, role=role)

    def setUp(self):
        cache.clear()

    def group_url(self, name, *extra):
        return reverse(name, args=(self.group.pk, self.group.slug, *extra))

    def course_url(self, name, *extra):
        return reverse(name, args=(self.course.slug, *extra))

    def test_saving_public_approval_lists_group_without_enabling_direct_join(self):
        self.assertFalse(self.group.is_listed_to(None))
        form = EditOrganizationForm(
            data={
                "name": self.group.name,
                "slug": self.group.slug,
                "short_name": self.group.short_name,
                "about": self.group.about,
                "visibility_mode": "public",
                "participation_mode": "approval",
                "admins": [self.manager.pk],
            },
            instance=self.group,
            profile=self.manager,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(self.group.is_listed_to(None))
        self.assertTrue(self.group.can_request_membership)
        self.assertFalse(self.group.can_join_now)
        self.assertContains(
            self.client.get(reverse("organization_list"), {"tab": "public"}),
            self.group.name,
        )
        self.assertIn(self.group.id, {pk for pk, slug in OrganizationSitemap().items()})

    def test_group_approval_deduplicates_and_only_manager_can_review(self):
        self.client.force_login(self.outsider.user)
        self.client.post(self.group_url("join_organization"))
        self.assertFalse(self.group.is_member(self.outsider))
        for _ in range(2):
            response = self.client.post(
                self.group_url("request_organization"), {"reason": "Xin tham gia"}
            )
            self.assertEqual(response.status_code, 302)
        pending = self.group.requests.get(user=self.outsider)
        self.assertEqual(pending.state, "P")
        self.assertContains(
            self.client.get(self.group_url("organization_home")),
            gettext("Pending approval"),
        )
        review_url = self.group_url("organization_requests_pending")
        payload = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-id": pending.pk,
            "form-0-state": "A",
        }
        self.assertEqual(self.client.post(review_url, payload).status_code, 403)
        self.assertFalse(self.group.members.filter(pk=self.outsider.pk).exists())
        self.client.force_login(self.manager.user)
        self.assertEqual(self.client.post(review_url, payload).status_code, 302)
        self.assertTrue(self.group.members.filter(pk=self.outsider.pk).exists())

    def test_suspended_group_hides_group_and_course_from_members(self):
        self.group.is_suspended = True
        self.group.save(update_fields=["is_suspended"])
        self.client.force_login(self.student.user)
        self.assertNotContains(
            self.client.get(reverse("organization_list"), {"tab": "mine"}),
            self.group.name,
        )
        self.assertEqual(
            self.client.get(self.group_url("organization_home")).status_code, 403
        )
        self.assertEqual(
            self.client.get(self.course_url("course_detail")).status_code, 404
        )
        invitation = self.course_url("course_invite", self.course.invite_token)
        self.assertEqual(self.client.get(invitation).status_code, 404)
        self.assertEqual(
            self.client.get(
                self.group_url("join_organization"), {"code": self.group.access_code}
            ).status_code,
            403,
        )
        self.client.force_login(self.manager.user)
        self.assertContains(
            self.client.get(self.group_url("organization_home")), gettext("Suspended")
        )
        self.assertEqual(
            self.client.get(self.course_url("course_detail")).status_code, 200
        )
        self.assertTrue(
            CourseRole.objects.filter(course=self.course, user=self.student).exists()
        )

    def test_course_approval_checks_audience_then_enrolls(self):
        self.client.force_login(self.outsider.user)
        self.client.post(self.course_url("course_join"))
        self.assertFalse(self.course.join_requests.exists())
        self.client.force_login(self.applicant.user)
        self.assertContains(
            self.client.get(self.course_url("course_detail")),
            gettext("Request to join"),
        )
        for _ in range(2):
            self.assertEqual(
                self.client.post(self.course_url("course_join")).status_code, 302
            )
        pending = self.course.join_requests.get(user=self.applicant)
        self.assertEqual(pending.state, CourseJoinRequest.State.PENDING)
        self.assertFalse(
            CourseRole.objects.filter(course=self.course, user=self.applicant).exists()
        )
        self.assertContains(
            self.client.get(self.course_url("course_detail")),
            gettext("Pending approval"),
        )
        self.assertEqual(
            self.client.get(self.course_url("course_grades")).status_code, 403
        )
        review = self.course_url("course_review_join_request", pending.pk)
        self.assertEqual(
            self.client.post(review, {"decision": "approve"}).status_code, 404
        )
        self.client.force_login(self.manager.user)
        self.assertContains(
            self.client.get(self.course_url("course_members")), self.applicant.username
        )
        self.assertEqual(
            self.client.post(review, {"decision": "approve"}).status_code, 302
        )
        pending.refresh_from_db()
        self.assertEqual(pending.state, CourseJoinRequest.State.APPROVED)
        self.assertEqual(pending.reviewed_by, self.manager)
        self.assertTrue(Course.is_accessible_by(self.course, self.applicant))

    def test_group_direct_join_is_available_after_restoring_visibility(self):
        self.group.is_open = True
        self.group.is_suspended = True
        self.group.save(update_fields=["is_open", "is_suspended"])
        self.client.force_login(self.outsider.user)
        direct = self.group_url("join_organization")
        self.assertEqual(self.client.post(direct).status_code, 403)
        self.group.is_suspended = False
        self.group.save(update_fields=["is_suspended"])
        self.assertContains(
            self.client.get(self.group_url("organization_home")), gettext("Join now")
        )
        self.assertEqual(self.client.post(direct).status_code, 302)
        self.assertTrue(self.group.members.filter(pk=self.outsider.pk).exists())

    def test_suspended_membership_does_not_grant_access_to_group_only_content(self):
        problem = Problem.objects.create(
            code="visibilitygroup",
            name="Bài riêng của Nhóm",
            is_public=True,
            group=ProblemGroup.objects.create(name="vis", full_name="Visibility"),
            time_limit=1,
            memory_limit=65536,
            points=100,
        )
        problem.organizations.add(self.group)
        contest = Contest.objects.create(
            key="visibilitygroup",
            name="Thi trong Nhóm",
            is_visible=True,
            start_time=timezone.now() - timezone.timedelta(hours=2),
            end_time=timezone.now() + timezone.timedelta(hours=1),
        )
        contest.organizations.add(self.group)
        self.assertTrue(
            Problem.objects.get(pk=problem.pk).is_accessible_by(self.student.user)
        )
        self.assertTrue(
            Contest.objects.get(pk=contest.pk).is_accessible_by(self.student.user)
        )
        self.group.is_suspended = True
        self.group.save(update_fields=["is_suspended"])
        self.assertFalse(
            Problem.objects.get(pk=problem.pk).is_accessible_by(self.student.user)
        )
        self.assertFalse(
            Contest.objects.get(pk=contest.pk).is_accessible_by(self.student.user)
        )
        self.assertFalse(
            Contest.get_visible_contests(self.student.user)
            .filter(pk=contest.pk)
            .exists()
        )

    def test_direct_enrollment_and_pause_explicitly_gate_posts(self):
        self.course.is_open = True
        self.course.is_suspended = True
        self.course.save(update_fields=["is_open", "is_suspended"])
        self.client.force_login(self.applicant.user)
        self.client.post(self.course_url("course_join"))
        self.assertFalse(
            CourseRole.objects.filter(course=self.course, user=self.applicant).exists()
        )
        self.client.force_login(self.student.user)
        self.assertEqual(
            self.client.get(self.course_url("course_grades")).status_code, 403
        )
        self.assertNotContains(
            self.client.get(reverse("course_list"), {"tab": "my"}), self.course.name
        )
        self.course.is_suspended = False
        self.course.save(update_fields=["is_suspended"])
        self.client.force_login(self.applicant.user)
        self.assertContains(
            self.client.get(self.course_url("course_detail")), gettext("Join now")
        )
        self.client.post(self.course_url("course_join"))
        self.assertFalse(self.course.join_requests.exists())
        self.assertTrue(
            CourseRole.objects.filter(course=self.course, user=self.applicant).exists()
        )

    def test_course_review_revalidates_group_membership_and_rejects(self):
        pending = self.course.join_requests.create(user=self.applicant)
        self.group.members.remove(self.applicant)
        self.client.force_login(self.manager.user)
        review = self.course_url("course_review_join_request", pending.pk)
        self.client.post(review, {"decision": "approve"})
        pending.refresh_from_db()
        self.assertEqual(pending.state, CourseJoinRequest.State.PENDING)
        self.client.post(review, {"decision": "reject"})
        pending.refresh_from_db()
        self.assertEqual(pending.state, CourseJoinRequest.State.REJECTED)
        self.assertFalse(
            CourseRole.objects.filter(course=self.course, user=self.applicant).exists()
        )
