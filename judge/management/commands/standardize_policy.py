"""Manage auto-apply policies (PR-H, spec §7.2).

manage.py standardize_policy list
manage.py standardize_policy create --policy tags_v1 --name "tags, private" --groups tags \
    [--steps s] [--prompt-versions v] [--from-id A --through-id B] [--batch-id N] [--include-public] \
    [--daily-cap 100] [--total-cap N] [--sample-rate 0.2] [--thresholds '{"tags": {"min_confidence": "high"}}']
manage.py standardize_policy enable|disable|resume --policy tags_v1
manage.py standardize_policy evaluate --policy tags_v1 --proposal-id 123     # dry run, prints the 7 conditions
manage.py standardize_policy sweep [--policy tags_v1] [--limit 200] [--dry-run]
manage.py standardize_policy audit --policy tags_v1                            # pending samples + breaker stats
manage.py standardize_policy audit --sample-id 5 --verdict ok|wrong [--actor user] [--note ...]
"""

import json

from django.core.management.base import BaseCommand, CommandError

from judge.models import Profile
from judge.models.standardization import (
    StandardizationAuditSample,
    StandardizationPolicy,
    StandardizationProposal,
)
from judge.standardization import policy as policy_mod
from judge.standardization.enums import AuditVerdict

ACTIONS = (
    "list",
    "create",
    "enable",
    "disable",
    "resume",
    "evaluate",
    "sweep",
    "audit",
)


def _csv(value):
    return [x.strip() for x in value.split(",") if x.strip()] if value else []


class Command(BaseCommand):
    help = "Create, switch, evaluate and sweep standardization auto-apply policies."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=ACTIONS)
        parser.add_argument("--policy")
        parser.add_argument("--name", default="")
        parser.add_argument("--groups", help="comma-separated field groups")
        parser.add_argument("--steps", help="comma-separated steps (empty = any)")
        parser.add_argument("--prompt-versions", help="comma-separated (empty = any)")
        parser.add_argument("--from-id", type=int, default=0)
        parser.add_argument("--through-id", type=int, default=0)
        parser.add_argument("--batch-id", type=int)
        parser.add_argument("--include-public", action="store_true")
        parser.add_argument("--daily-cap", type=int, default=100)
        parser.add_argument("--total-cap", type=int)
        parser.add_argument("--sample-rate", type=float, default=0.2)
        parser.add_argument("--thresholds", help="JSON object keyed by field group")
        parser.add_argument("--proposal-id", type=int)
        parser.add_argument("--sample-id", type=int)
        parser.add_argument("--verdict", choices=[AuditVerdict.OK, AuditVerdict.WRONG])
        parser.add_argument("--actor", help="username recorded on the audit/resume")
        parser.add_argument("--note", default="")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--dry-run", action="store_true")

    def _dump(self, obj):
        self.stdout.write(json.dumps(obj, ensure_ascii=False, default=str))

    def _policy(self, options):
        if not options["policy"]:
            raise CommandError("--policy <version> is required")
        policy = StandardizationPolicy.objects.filter(version=options["policy"]).first()
        if policy is None:
            raise CommandError(f"no policy with version {options['policy']!r}")
        return policy

    def _actor(self, options):
        if not options["actor"]:
            return None
        actor = Profile.objects.filter(user__username=options["actor"]).first()
        if actor is None:
            raise CommandError(f"no profile for username {options['actor']!r}")
        return actor

    @staticmethod
    def _row(policy):
        return {
            "id": policy.pk,
            "version": policy.version,
            "name": policy.name,
            "enabled": policy.is_enabled,
            "halted": policy.halted,
            "halted_reason": policy.halted_reason,
            "groups": policy.groups_json,
            "steps": policy.steps_json,
            "prompt_versions": policy.prompt_versions_json,
            "id_from": policy.id_from,
            "id_through": policy.id_through,
            "batch": policy.batch_id,
            "include_public": policy.include_public,
            "daily_cap": policy.daily_cap,
            "total_cap": policy.total_cap,
            "applied_today": policy_mod.applied_today(policy),
            "applied_count": policy.applied_count,
            "sample_rate": policy.sample_rate,
            "thresholds": {
                g: policy_mod.thresholds_for(policy, g) for g in policy.groups_json
            },
            "audit": policy_mod.audit_stats(policy),
        }

    def handle(self, *args, **options):
        getattr(self, f"do_{options['action']}")(options)

    def do_list(self, options):
        self._dump([self._row(p) for p in StandardizationPolicy.objects.order_by("id")])

    def do_create(self, options):
        if not options["policy"]:
            raise CommandError("--policy <version> is required")
        groups = _csv(options["groups"])
        bad = [g for g in groups if g not in policy_mod.POLICY_GROUPS]
        if not groups or bad:
            raise CommandError(
                f"--groups must name at least one of {list(policy_mod.POLICY_GROUPS)}"
                + (f"; unknown: {bad}" if bad else "")
            )
        thresholds = {}
        if options["thresholds"]:
            try:
                thresholds = json.loads(options["thresholds"])
            except ValueError as exc:
                raise CommandError(f"--thresholds is not JSON: {exc}")
            if not isinstance(thresholds, dict):
                raise CommandError("--thresholds must be a JSON object")
        if StandardizationPolicy.objects.filter(version=options["policy"]).exists():
            raise CommandError(f"version {options['policy']!r} already exists; bump it")
        policy = StandardizationPolicy.objects.create(
            version=options["policy"],
            name=options["name"] or options["policy"],
            is_enabled=False,
            groups_json=groups,
            steps_json=_csv(options["steps"]),
            prompt_versions_json=_csv(options["prompt_versions"]),
            id_from=options["from_id"],
            id_through=options["through_id"],
            batch_id=options["batch_id"],
            include_public=options["include_public"],
            thresholds_json=thresholds,
            daily_cap=options["daily_cap"],
            total_cap=options["total_cap"],
            sample_rate=options["sample_rate"],
            created_by=self._actor(options),
        )
        self.stdout.write(
            "created (disabled); enable with: standardize_policy enable --policy ..."
        )
        self._dump(self._row(policy))

    def do_enable(self, options):
        policy = self._policy(options)
        policy.is_enabled = True
        policy.save(update_fields=["is_enabled"])
        self._dump(self._row(policy))

    def do_disable(self, options):
        policy = self._policy(options)
        policy.is_enabled = False
        policy.save(update_fields=["is_enabled"])
        self._dump(self._row(policy))

    def do_resume(self, options):
        policy = self._policy(options)
        policy_mod.resume(policy, self._actor(options), note=options["note"])
        self._dump(self._row(policy))

    def do_evaluate(self, options):
        policy = self._policy(options)
        if not options["proposal_id"]:
            raise CommandError("--proposal-id is required")
        proposal = StandardizationProposal.objects.filter(
            pk=options["proposal_id"]
        ).first()
        if proposal is None:
            raise CommandError("no such proposal")
        ev = policy_mod.evaluate(proposal, policy)
        for name, (ok, detail) in ev.conditions.items():
            self.stdout.write(f"  {'PASS' if ok else 'HOLD'} {name:<18} {detail}")
        self._dump(ev.as_json())

    def do_sweep(self, options):
        policies = (
            [self._policy(options)]
            if options["policy"]
            else list(
                StandardizationPolicy.objects.filter(is_enabled=True, halted=False)
            )
        )
        out = []
        for policy in policies:
            if options["dry_run"]:
                held, would = {}, []
                for proposal in policy_mod.candidates(policy, limit=options["limit"]):
                    ev = policy_mod.evaluate(proposal, policy)
                    (
                        would.append(proposal.pk)
                        if ev.passed
                        else held.__setitem__(str(proposal.pk), ev.reasons)
                    )
                out.append(
                    {
                        "policy": policy.version,
                        "dry_run": True,
                        "would_apply": would,
                        "held": held,
                    }
                )
            else:
                out.append(policy_mod.sweep(policy, limit=options["limit"]))
        self._dump(out)

    def do_audit(self, options):
        if options["sample_id"]:
            if not options["verdict"]:
                raise CommandError("--verdict ok|wrong is required with --sample-id")
            sample = StandardizationAuditSample.objects.filter(
                pk=options["sample_id"]
            ).first()
            if sample is None:
                raise CommandError("no such sample")
            sample, halted = policy_mod.record_audit(
                sample, options["verdict"], self._actor(options), note=options["note"]
            )
            self._dump(
                {
                    "sample": sample.pk,
                    "verdict": sample.verdict,
                    "policy_halted": halted,
                    "audit": policy_mod.audit_stats(sample.policy),
                }
            )
            return
        policy = self._policy(options)
        pending = [
            {
                "sample": s.pk,
                "proposal": s.proposal_id,
                "problem": s.proposal.problem.code,
                "group": s.proposal.field_group,
                "applied_at": s.proposal.applied_at,
            }
            for s in StandardizationAuditSample.objects.filter(
                policy=policy, verdict=AuditVerdict.PENDING
            ).select_related("proposal__problem")[:200]
        ]
        self._dump(
            {
                "policy": policy.version,
                "audit": policy_mod.audit_stats(policy),
                "pending": pending,
            }
        )
