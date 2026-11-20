"""Apply scoped policies after a completed run, then classify exceptions."""

from django.conf import settings
from django.db.models import Q
from hashlib import sha256

from judge.models import Problem
from judge.models.standardization import StandardizationPolicy


def finalize_run(batch):
    from judge.standardization.routing import refresh_routes

    if batch.status != "finished":
        return {"skipped": "unfinished"}
    from judge.models.standardization import StandardizationBatch
    from judge.standardization.guided import ACTIVE, VERSION

    if StandardizationBatch.objects.filter(
        config_version=VERSION, id_from=batch.id_from, status__in=ACTIVE
    ).exists():
        return {"skipped": "newer_run_active"}
    problem = Problem.objects.filter(pk=batch.id_from).first()
    if not problem:
        return {"skipped": "problem_missing"}
    batch.refresh_from_db()
    results = []
    if getattr(settings, "STANDARDIZATION_GUIDED_AUTO_APPLY", False):
        from judge.standardization.policy import (
            candidates,
            auto_apply,
            evaluate,
            refresh_guided_verification,
        )
        from judge.standardization.independent_review import run_review
        from judge.standardization.verification import (
            current_full_verification,
            verify_solution_proposal,
            _language_for,
        )
        from judge.standardization.hashing import compute_judge_hash
        from judge.models.standardization import StandardizationProposal

        policies = (
            StandardizationPolicy.objects.filter(
                is_enabled=True, halted=False, id_from__lte=problem.pk
            )
            .filter(Q(id_through=0) | Q(id_through__gte=problem.pk))
            .filter(Q(batch__isnull=True) | Q(batch=batch))
        )
        policies = list(policies)
        # Existing AI rewrites may fail preservation even when the original
        # source can be formatted mechanically. Reuse the source, not a new AI
        # call. Create this replacement only with an in-scope rule policy.
        from judge.standardization import format_v1
        from judge.standardization.readiness import source_readiness
        from judge.standardization.guided import rule_proposal
        from judge.standardization.enums import FieldGroup

        rule_scope = any(
            "content" in p.groups_json
            and VERSION in (p.prompt_versions_json or [])
            and (not p.steps_json or VERSION in p.steps_json)
            and (not problem.is_public or p.include_public)
            for p in policies
        )
        if rule_scope:
            from judge.standardization.hashing import compute_source_hash

            version = compute_source_hash(problem)
            unchanged = batch.proposals.filter(
                Q(snapshot__source_hash=version)
                | Q(status="applied", evidence_json__auto_apply_after_hash=version)
            ).exists()
            latest_content = (
                StandardizationProposal.objects.filter(
                    problem=problem, field_group="content"
                )
                .order_by("-created_at", "-pk")
                .first()
            )
            human_hold = latest_content and (
                latest_content.reviewed_at
                or latest_content.decisions.exists()
                or latest_content.status in ("rejected", "restored", "rework")
            )
            report = source_readiness(problem)
            fixed, preservation = format_v1.preserve_format(problem.description or "")
            if unchanged and not human_hold and report.ready and preservation:
                rule_proposal(
                    problem,
                    batch,
                    report.classification,
                    FieldGroup.CONTENT,
                    VERSION,
                    {"description": fixed},
                    {},
                    {
                        "preservation": preservation,
                        "validation": format_v1.validate(fixed).label,
                    },
                )
        for policy in policies:
            reviewed = set()
            for proposal in candidates(policy, problem_id=problem.pk):
                refresh_guided_verification(proposal, policy)
                profile = (policy.thresholds_json or {}).get("reference_review") or {}
                if profile.get("enabled") and proposal.field_group in (
                    "solution",
                    "tags",
                    "points",
                ):
                    solution = (
                        proposal
                        if proposal.field_group == "solution"
                        else StandardizationProposal.objects.filter(
                            pk=proposal.evidence_json.get("solution_proposal_id"),
                            problem_id=problem.pk,
                            field_group="solution",
                        ).first()
                    )
                    preflight = evaluate(proposal, policy)
                    # Source, scope, caps and staleness still gate spending.
                    allowed = all(
                        preflight.conditions[name][0]
                        for name in ("policy", "source", "problem_unchanged")
                    )
                    allowed = (
                        allowed
                        and getattr(settings, "STANDARDIZATION_REVIEW_MODEL", "")
                        in (profile.get("reviewer_models") or [])
                        and policy.sample_rate >= 0.1
                        and policy.daily_cap <= 200
                    )
                    if solution and solution.pk not in reviewed and allowed:
                        reviewed.add(solution.pk)
                        if (
                            not current_full_verification(solution)
                            and solution.verifications.filter(
                                details_json__reason="full_score"
                            ).exists()
                        ):
                            same = solution.verifications.filter(
                                test_config_hash=compute_judge_hash(
                                    solution.problem, _language_for(solution)
                                ),
                                details_json__source_hash=sha256(
                                    (
                                        solution.payload_json.get("cpp_code") or ""
                                    ).encode()
                                ).hexdigest(),
                            )
                            # Pending polls and definitive failures must not be
                            # resubmitted each time a finalizer/sweep is called.
                            if not same.filter(
                                level__in=("failed", "unknown")
                            ).exists():
                                verify_solution_proposal(solution)
                        review = run_review(solution)
                        results.append({"review": solution.pk, **review})
                        from judge.standardization.independent_review import context
                        from django.db import transaction

                        with transaction.atomic():
                            current = (
                                StandardizationProposal.objects.select_for_update().get(
                                    pk=solution.pk
                                )
                            )
                            evidence = dict(current.evidence_json or {})
                            if review.get("error"):
                                evidence["independent_review_error"] = {
                                    "code": review["error"],
                                    "identity": context(current),
                                }
                            elif review.get("verification"):
                                evidence.pop("independent_review_error", None)
                            current.evidence_json = evidence
                            current.save(update_fields=["evidence_json"])
                        if review.get("error"):
                            from judge.models.standardization import (
                                StandardizationDisposition,
                            )
                            from judge.standardization.readiness import fingerprint

                            StandardizationDisposition.objects.update_or_create(
                                problem=problem,
                                field_group="solution",
                                defaults={
                                    "state": "retry_infra",
                                    "reason_code": review["error"],
                                    "fingerprint": fingerprint(problem),
                                    "batch": batch,
                                    "details_json": {
                                        "category": "infrastructure",
                                        "proposal_id": solution.pk,
                                    },
                                },
                            )
                ev, applied = auto_apply(proposal, policy)
                results.append(
                    {
                        "proposal": proposal.pk,
                        "applied": bool(applied),
                        "reasons": ev.reasons,
                    }
                )
    batch.refresh_from_db()
    refresh_routes(problem, batch)
    return {"results": results}
