from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from judge.models import Language, Problem, ProblemGroup, Profile
from judge.models.standardization import ProblemSnapshot, StandardizationProposal
from judge.standardization.enums import FieldGroup, ProposalStatus
from judge.standardization.hashing import compute_source_hash
from judge.standardization.snapshot import capture_all
from judge.tests.test_standardization_apply import ensure_languages

SECRET = "Đề riêng tư có từ khóa BIMAT_XYZ."
DRAFT = "Đề mới DRAFT_ABC.\n\n### Input\n- x\n\n### Output\n- y\n\n### Example\n- (không có)\n"


class StandardizationViewsTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        ensure_languages()
        lang = Language.objects.first()
        cls.group = ProblemGroup.objects.create(name="stdvw", full_name="std views")
        cls.admin = User.objects.create_superuser("stdadmin", "a@x.com", "x")
        Profile.objects.create(user=cls.admin, language=lang)
        cls.student = User.objects.create_user("stdstudent", "s@x.com", "x")
        Profile.objects.create(user=cls.student, language=lang)
        cls.viewer = User.objects.create_user("stdviewer", "v@x.com", "x")
        Profile.objects.create(user=cls.viewer, language=lang)
        cls.viewer.user_permissions.add(
            Permission.objects.get(codename="view_standardizationproposal")
        )
        cls.reviewer = User.objects.create_user("stdreviewer", "r@x.com", "x")
        Profile.objects.create(user=cls.reviewer, language=lang)
        cls.reviewer.user_permissions.add(
            Permission.objects.get(codename="view_standardizationproposal"),
            Permission.objects.get(codename="review_standardization"),
        )

    def setUp(self):
        self.problem = Problem.objects.create(
            code="stdvw1",
            name="Views",
            description=SECRET,
            group=self.group,
            time_limit=1,
            memory_limit=1048576,
            points=100,
            is_public=False,
        )
        snapshot = ProblemSnapshot.objects.create(
            problem=self.problem,
            source_hash=compute_source_hash(self.problem),
            before_json=capture_all(self.problem),
        )
        self.proposal = StandardizationProposal.objects.create(
            snapshot=snapshot,
            problem=self.problem,
            field_group=FieldGroup.CONTENT,
            step="draft_content_v1",
            prompt_version="t",
            dedupe_key="vk1",
            status=ProposalStatus.PENDING_REVIEW,
            payload_json={"description": DRAFT},
            cost_usd="0.123456",
        )
        self.points = StandardizationProposal.objects.create(
            snapshot=snapshot,
            problem=self.problem,
            field_group=FieldGroup.POINTS,
            step="tags_points_v1",
            prompt_version="t",
            dedupe_key="vk2",
            status=ProposalStatus.PENDING_REVIEW,
            payload_json={
                "points": 1200,
                "range": [1100, 1300],
                "confidence": "medium",
                "label": "x",
            },
        )
        self.page = reverse("internal_standardization_problem", args=(self.problem.pk,))
        self.queue = reverse("internal_standardization_queue")

    def action(self, proposal, name):
        return reverse("internal_standardization_action", args=(proposal.pk, name))

    def test_anonymous_redirects_and_student_forbidden(self):
        self.assertEqual(self.client.get(self.page).status_code, 302)
        self.assertEqual(self.client.get(self.queue).status_code, 302)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.assertEqual(self.client.get(self.queue).status_code, 403)
        r = self.client.post(self.action(self.proposal, "approve"))
        self.assertEqual(r.status_code, 403)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ProposalStatus.PENDING_REVIEW)

    def test_admin_sees_source_diff_and_buttons(self):
        self.client.force_login(self.admin)
        r = self.client.get(self.page)
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("BIMAT_XYZ", body)
        self.assertIn("DRAFT_ABC", body)
        self.assertIn("Duyệt", body)
        self.assertIn("0.123456", body)  # cost visible to superuser
        self.assertIn("Points đề xuất", body)
        self.assertIn("1200", body)
        self.assertNotIn("Áp dụng", body)  # nothing approved yet
        self.assertEqual(r["Cache-Control"], "private, no-store")
        q = self.client.get(self.queue)
        self.assertEqual(q.status_code, 200)
        self.assertIn("stdvw1", q.content.decode())

    def test_viewer_without_source_rights_sees_status_not_text(self):
        self.client.force_login(self.viewer)
        r = self.client.get(self.page)
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        # has view_standardizationproposal, so can_view_problem_source is True by design;
        # but no review/apply/cost rights:
        self.assertNotIn("Duyệt", body)
        self.assertNotIn("0.123456", body)
        self.assertEqual(
            self.client.post(self.action(self.proposal, "approve")).status_code, 403
        )

    def test_review_then_apply_then_restore_flow(self):
        self.client.force_login(self.reviewer)
        r = self.client.post(self.action(self.proposal, "approve"), {"note": "ok"})
        self.assertEqual(r.status_code, 302)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ProposalStatus.APPROVED)
        self.assertEqual(self.proposal.review_note, "ok")
        # reviewer cannot apply
        self.assertEqual(
            self.client.post(self.action(self.proposal, "apply")).status_code, 403
        )
        self.client.force_login(self.admin)
        body = self.client.get(self.page).content.decode()
        self.assertIn("Áp dụng", body)
        r = self.client.post(
            self.action(self.proposal, "apply"), HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "status": "applied"})
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.description, DRAFT)
        # conflicts are per field group: the points proposal is still appliable
        body = self.client.get(self.page).content.decode()
        self.assertNotIn("nguồn đã thay đổi", body)
        self.client.post(self.action(self.points, "approve"))
        r = self.client.post(self.action(self.points, "apply"))
        self.assertEqual(r.status_code, 302)
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.points, 1200)  # private problem, cap bypassed
        # someone edits the statement by hand -> applied content proposal is stale
        self.problem.description = DRAFT + "\nsửa tay"
        self.problem.save()
        self.assertIn("nguồn đã thay đổi", self.client.get(self.page).content.decode())
        r = self.client.post(self.action(self.proposal, "restore"))
        self.assertEqual(r.status_code, 409)
        self.assertIn("Không thực hiện được", r.content.decode())
        # put it back and restore for real
        self.problem.description = DRAFT
        self.problem.save()
        r = self.client.post(self.action(self.proposal, "restore"))
        self.assertEqual(r.status_code, 302)
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.description, SECRET)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ProposalStatus.RESTORED)

    def test_unknown_action_and_wrong_method(self):
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(self.action(self.proposal, "explode")).status_code, 403
        )
        self.assertEqual(
            self.client.get(self.action(self.proposal, "approve")).status_code, 405
        )

    def test_parked_sources_have_separate_filter_even_without_proposals(self):
        from judge.models.standardization import StandardizationDisposition

        self.client.force_login(self.viewer)
        StandardizationDisposition.objects.create(
            problem=self.problem,
            field_group="",
            state="deferred_source",
            fingerprint="a" * 64,
            reason_code="statement_missing",
        )
        response = self.client.get(self.queue)
        self.assertNotIn("stdvw1", response.content.decode())
        response = self.client.get(self.queue + "?route=deferred_source&status=")
        self.assertIn("stdvw1", response.content.decode())
        self.assertIn("statement_missing", response.content.decode())

    def test_issue_list_includes_nonpending_fields_and_filters_by_category(self):
        from judge.models.standardization import StandardizationDisposition

        self.client.force_login(self.viewer)
        StandardizationDisposition.objects.create(
            problem=self.problem,
            field_group="tags",
            state="deferred_verification",
            fingerprint="b" * 64,
            reason_code="tag_evidence_required",
            details_json={"category": "tags", "action": "Xác nhận tag."},
        )
        response = self.client.get(self.queue + "?route=all&status=&issue=tags")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Xác nhận tag.", response.content.decode())
        response = self.client.get(self.queue + "?route=all&status=&issue=points")
        self.assertNotIn("Xác nhận tag.", response.content.decode())

    def test_certifying_editorial_requires_review_permission_and_solution(self):
        self.client.force_login(self.viewer)
        self.assertEqual(
            self.client.post(self.action(self.proposal, "certify")).status_code, 403
        )
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(self.action(self.proposal, "certify")).status_code, 409
        )

    def test_inventory_links_to_standardization(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("internal_problem_inventory") + "?q=stdvw1")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.page, r.content.decode())
