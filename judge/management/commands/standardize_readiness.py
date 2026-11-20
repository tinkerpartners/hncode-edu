"""Inspect/park sources without calling AI or changing a problem's fields."""

import json

from django.core.management.base import BaseCommand, CommandError

from judge.models import Problem
from judge.standardization.readiness import source_readiness, save_readiness


class Command(BaseCommand):
    help = "Check source/test readiness; --save persists routing only. Never applies proposals or calls AI."

    def add_arguments(self, parser):
        parser.add_argument(
            "--codes", required=True, help="Comma separated problem codes"
        )
        parser.add_argument("--save", action="store_true")

    def handle(self, *args, **options):
        codes = list(dict.fromkeys(filter(None, options["codes"].split(","))))
        problems = Problem.objects.in_bulk(codes, field_name="code")
        missing = sorted(set(codes) - set(problems))
        if missing:
            raise CommandError("Unknown problems: " + ", ".join(missing))
        for code in codes:
            report = source_readiness(problems[code])
            if options["save"]:
                save_readiness(problems[code], report)
            self.stdout.write(
                json.dumps({"code": code, **report.as_json()}, ensure_ascii=False)
            )
