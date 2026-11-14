import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from judge.utils.problem_inventory import inventory_queryset, inventory_rows


class Command(BaseCommand):
    help = "Read-only problem inventory as JSON lines. Resume with --after-id."

    def add_arguments(self, parser):
        parser.add_argument("--after-id", type=int, default=0)
        parser.add_argument("--through-id", type=int)
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        if options["after_id"] < 0 or not 1 <= options["batch_size"] <= 500:
            raise CommandError(
                "after-id must be nonnegative, batch-size must be 1..500"
            )
        queryset = inventory_queryset()
        upper = options["through_id"]
        if upper is None:
            upper = queryset.values_list("pk", flat=True).last() or 0
        last_id = options["after_id"]
        states = Counter()
        count = 0
        # Exclusive creation avoids accidentally overwriting a previous audit.
        try:
            with open(options["output"], "x", encoding="utf-8") as output:
                while True:
                    batch = list(
                        queryset.filter(pk__gt=last_id, pk__lte=upper)[
                            : options["batch_size"]
                        ]
                    )
                    if not batch:
                        break
                    for row in inventory_rows(batch):
                        output.write(json.dumps(row, ensure_ascii=False) + "\n")
                        last_id = row["id"]
                        states[row["statement"]["state"]] += 1
                        count += 1
                    output.flush()
        except FileExistsError:
            raise CommandError(
                "Output exists. Choose a new path to keep the previous audit."
            )
        self.stdout.write(
            json.dumps(
                {
                    "rows": count,
                    "last_id": last_id,
                    "through_id": upper,
                    "states": states,
                },
                ensure_ascii=False,
            )
        )
