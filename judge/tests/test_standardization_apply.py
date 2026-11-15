from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from judge.models import (
    Contest,
    ContestProblem,
    Language,
    Problem,
    ProblemGroup,
    ProblemType,
    Profile,
    Submission,
)
from judge.models.problem import Solution
from judge.models.standardization import (
    ProblemSnapshot,
    StandardizationDecision,
    StandardizationProposal,
    StandardizationRescoreJob,
)
from judge.standardization.apply import (
    ContestBlockedError,
    ProposalStateError,
    RestoreConflictError,
    StaleSourceError,
    apply_proposal,
    restore_proposal,
    review_proposal,
    transition,
)
from judge.standardization.config_profile import REQUIRED_LANGUAGE_KEYS
from judge.standardization.enums import (
    DecisionAction,
    FieldGroup,
    ProposalStatus,
    RescoreStatus,
)
from judge.standardization.hashing import compute_source_hash, dedupe_key
from judge.standardization.snapshot import capture_all


def ensure_languages():
    for key in REQUIRED_LANGUAGE_KEYS:
        Language.objects.get_or_create(
            key=key,
            defaults={"name": key, "short_name": key, "common_name": key,
                      "ace": "c_cpp", "pygments": "cpp", "template": ""},
        )


@override_settings(STANDARDIZATION_AUTHOR_USERNAME="MrTee")
class ApplyProposalTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        ensure_languages()
        cls.group = ProblemGroup.objects.create(name="stda", full_name="std apply")
        cls.lang = Language.get_python3()
        cls.mrtee = Profile.objects.create(
            user=User.objects.create_user("MrTee", "m@x.com", "x"), language=cls.lang
        )
        cls.reviewer = Profile.objects.create(
            user=User.objects.create_user("reviewer", "r@x.com", "x"), language=cls.lang
        )
        cls.tag_dp = ProblemType.objects.create(name="dp", full_name="DP")
        cls.tag_greedy = ProblemType.objects.create(name="greedy", full_name="Greedy")

    def setUp(self):
        self.problem = Problem.objects.create(
            code="stdapply1", name="Apply", description="Đề cũ.", group=self.group,
            time_limit=1, memory_limit=262144, points=100, partial=False, is_public=False,
        )
        self.problem.types.add(self.tag_greedy)

    def snapshot(self):
        self.problem = Problem.objects.get(pk=self.problem.pk)
        return ProblemSnapshot.objects.create(
            problem=self.problem,
            source_hash=compute_source_hash(self.problem),
            before_json=capture_all(self.problem),
        )

    def proposal(self, group, payload, status=ProposalStatus.APPROVED, step="t", snapshot=None):
        snapshot = snapshot or self.snapshot()
        return StandardizationProposal.objects.create(
            snapshot=snapshot, problem=self.problem, field_group=group, step=step,
            dedupe_key=dedupe_key(self.problem.id, snapshot.source_hash, group, step, ""),
            status=status, payload_json=payload,
        )

    def test_refuses_unapproved(self):
        p = self.proposal(FieldGroup.CONTENT, {"description": "x"}, status=ProposalStatus.PENDING_REVIEW)
        with self.assertRaises(ProposalStateError):
            apply_proposal(p, actor=self.reviewer)
        self.assertEqual(Problem.objects.get(pk=self.problem.pk).description, "Đề cũ.")

    def test_apply_content_records_audit_and_revision(self):
        p = self.proposal(FieldGroup.CONTENT, {"description": "### Input\nmới"})
        apply_proposal(p, actor=self.reviewer)
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.APPLIED)
        self.assertEqual(p.applied_by, self.reviewer)
        self.assertEqual(p.after_json, {"description": "### Input\nmới"})
        self.assertEqual(Problem.objects.get(pk=self.problem.pk).description, "### Input\nmới")
        d = StandardizationDecision.objects.get(proposal=p, action=DecisionAction.APPLY)
        self.assertEqual(d.before_json, {"description": "Đề cũ."})
        self.assertEqual(d.actor, self.reviewer)
        from reversion.models import Version
        self.assertTrue(Version.objects.get_for_object(self.problem).exists())

    def test_stale_when_group_changed_since_snapshot(self):
        p = self.proposal(FieldGroup.CONTENT, {"description": "AI"})
        self.problem.description = "Tác giả vừa sửa."
        self.problem.save()
        with self.assertRaises(StaleSourceError):
            apply_proposal(p, actor=self.reviewer)
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.STALE)
        self.assertEqual(Problem.objects.get(pk=self.problem.pk).description, "Tác giả vừa sửa.")
        self.assertTrue(
            StandardizationDecision.objects.filter(proposal=p, action=DecisionAction.CONFLICT).exists()
        )

    def test_other_group_change_does_not_stale(self):
        snap = self.snapshot()
        content = self.proposal(FieldGroup.CONTENT, {"description": "AI"}, snapshot=snap)
        config = self.proposal(FieldGroup.CONFIG, {"require_judge": False}, snapshot=snap, step="c")
        apply_proposal(config)  # changes partial/memory/languages/authors
        apply_proposal(content, actor=self.reviewer)  # must still apply
        content.refresh_from_db()
        self.assertEqual(content.status, ProposalStatus.APPLIED)

    def test_blocked_during_live_contest(self):
        now = timezone.now()
        contest = Contest.objects.create(
            key="stdlive", name="Live", description="d",
            start_time=now - timezone.timedelta(minutes=5),
            end_time=now + timezone.timedelta(hours=1),
        )
        ContestProblem.objects.create(contest=contest, problem=self.problem, points=100, order=0)
        p = self.proposal(FieldGroup.POINTS, {"points": 1500})
        with self.assertRaises(ContestBlockedError):
            apply_proposal(p, actor=self.reviewer)
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.BLOCKED_CONTEST)
        self.assertEqual(Problem.objects.get(pk=self.problem.pk).points, 100)

    def test_points_apply_bypasses_private_cap_and_schedules_rescore_once(self):
        Submission.objects.create(
            problem=self.problem, user=self.reviewer, language=self.lang,
            points=100, case_points=1, case_total=1, status="D", result="AC",
        )
        p = self.proposal(FieldGroup.POINTS, {"points": 1500})
        apply_proposal(p, actor=self.reviewer)
        self.assertEqual(Problem.objects.get(pk=self.problem.pk).points, 1500)  # private, not clamped
        jobs = StandardizationRescoreJob.objects.filter(problem=self.problem)
        self.assertEqual(jobs.count(), 1)
        self.assertEqual(jobs[0].status, RescoreStatus.PENDING)
        self.assertEqual(jobs[0].reason, "points")
        d = StandardizationDecision.objects.get(proposal=p, action=DecisionAction.APPLY)
        self.assertEqual(d.rescore_job, jobs[0])
        # same target state again -> still one job
        p2 = self.proposal(FieldGroup.POINTS, {"points": 1500}, step="t2")
        apply_proposal(p2, actor=self.reviewer)
        self.assertEqual(StandardizationRescoreJob.objects.filter(problem=self.problem).count(), 1)

    def test_no_rescore_job_without_submissions(self):
        p = self.proposal(FieldGroup.POINTS, {"points": 1500})
        apply_proposal(p, actor=self.reviewer)
        self.assertFalse(StandardizationRescoreJob.objects.filter(problem=self.problem).exists())

    def test_config_apply_and_restore(self):
        Solution.objects.create(
            problem=self.problem, content="s", is_public=True, publish_on=timezone.now()
        )
        p = self.proposal(FieldGroup.CONFIG, {"require_judge": False})
        apply_proposal(p, policy_version="mechanical_config_v1")
        prob = Problem.objects.get(pk=self.problem.pk)
        self.assertTrue(prob.partial)
        self.assertEqual(prob.memory_limit, 1048576)
        self.assertIn(self.mrtee.id, prob.authors.values_list("id", flat=True))
        self.assertFalse(Solution.objects.get(problem=prob).is_public)
        p.refresh_from_db()
        self.assertEqual(
            p.diff_json["rules_changed"],
            ["partial", "languages", "memory", "authors", "solution_private"],
        )
        d = StandardizationDecision.objects.get(proposal=p, action=DecisionAction.APPLY)
        self.assertEqual(d.actor, None)
        self.assertEqual(d.actor_kind, "service")
        self.assertEqual(d.policy_version, "mechanical_config_v1")

        restore_proposal(p, actor=self.reviewer)
        prob = Problem.objects.get(pk=self.problem.pk)
        self.assertFalse(prob.partial)
        self.assertEqual(prob.memory_limit, 262144)
        self.assertNotIn(self.mrtee.id, prob.authors.values_list("id", flat=True))
        self.assertFalse(Solution.objects.get(problem=prob).is_public)  # never re-published
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.RESTORED)

    def test_tags_apply_and_restore(self):
        p = self.proposal(FieldGroup.TAGS, {"type_ids": [self.tag_dp.id]})
        apply_proposal(p, actor=self.reviewer)
        self.assertEqual(
            list(self.problem.types.values_list("id", flat=True)), [self.tag_dp.id]
        )
        p.refresh_from_db()
        self.assertEqual(p.diff_json, {"added": [self.tag_dp.id], "removed": [self.tag_greedy.id]})
        restore_proposal(p, actor=self.reviewer)
        self.assertEqual(
            list(self.problem.types.values_list("id", flat=True)), [self.tag_greedy.id]
        )

    def test_solution_created_is_private_and_restore_deletes(self):
        p = self.proposal(FieldGroup.SOLUTION, {"content": "## 1. Tóm tắt"})
        apply_proposal(p, actor=self.reviewer)
        sol = Solution.objects.get(problem=self.problem)
        self.assertFalse(sol.is_public)
        self.assertEqual(sol.content, "## 1. Tóm tắt")
        restore_proposal(p, actor=self.reviewer)
        self.assertFalse(Solution.objects.filter(problem=self.problem).exists())

    def test_restore_conflict_when_group_edited_after_apply(self):
        p = self.proposal(FieldGroup.CONTENT, {"description": "AI"})
        apply_proposal(p, actor=self.reviewer)
        self.problem.description = "Sửa tay sau apply."
        self.problem.save()
        with self.assertRaises(RestoreConflictError):
            restore_proposal(p, actor=self.reviewer)
        self.assertEqual(Problem.objects.get(pk=self.problem.pk).description, "Sửa tay sau apply.")
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.APPLIED)

    def test_review_transitions(self):
        p = self.proposal(FieldGroup.CONTENT, {"description": "AI"}, status=ProposalStatus.PENDING_REVIEW)
        review_proposal(p, self.reviewer, DecisionAction.REWORK, note="thiếu ví dụ")
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.REWORK)
        self.assertEqual(p.review_note, "thiếu ví dụ")
        with self.assertRaises(ProposalStateError):
            review_proposal(p, self.reviewer, DecisionAction.APPROVE)
        transition(p, ProposalStatus.PENDING_REVIEW)
        review_proposal(p, self.reviewer, DecisionAction.APPROVE)
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.APPROVED)
        self.assertEqual(p.reviewed_by, self.reviewer)
        apply_proposal(p, actor=self.reviewer)
        with self.assertRaises(ProposalStateError):
            transition(p, ProposalStatus.APPROVED)
