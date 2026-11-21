"""Resume bounded existing guided results without regenerating their drafts."""

import json
from django.core.management.base import BaseCommand, CommandError
from judge.models import Problem, Profile
from judge.models.standardization import StandardizationBatch
from judge.standardization.guided import VERSION
from judge.standardization.permissions import (
    can_apply_standardization,
    can_view_problem_source,
)
from judge.standardization.production import finalize_run


class Command(BaseCommand):
    help = "Plan or finalize existing results. --execute may rejudge, review with AI and apply scoped enabled policies."

    def add_arguments(self, parser):
        parser.add_argument("--codes", required=True)
        parser.add_argument("--actor", required=True)
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        actor = (
            Profile.objects.select_related("user")
            .filter(user__username=options["actor"])
            .first()
        )
        if not actor or not can_apply_standardization(actor.user):
            raise CommandError("Actor needs apply_standardization permission")
        codes = list(dict.fromkeys(filter(None, options["codes"].split(","))))
        if not 1 <= len(codes) <= 100:
            raise CommandError("Use 1 to 100 problem codes")
        problems = Problem.objects.in_bulk(codes, field_name="code")
        if len(problems) != len(codes) or any(
            not can_view_problem_source(actor.user, p) for p in problems.values()
        ):
            raise CommandError("Unknown or inaccessible problem")
        for code in codes:
            batch = (
                StandardizationBatch.objects.filter(
                    config_version=VERSION, id_from=problems[code].pk
                )
                .order_by("-pk")
                .first()
            )
            result = (
                {"skipped": "no_finished_run"}
                if not batch or batch.status != "finished"
                else (
                    finalize_run(batch)
                    if options["execute"]
                    else {"would_finalize": batch.pk}
                )
            )
            self.stdout.write(json.dumps({"code": code, **result}, ensure_ascii=False))
