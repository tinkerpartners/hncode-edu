"""Shared best-result states for scoped problem pages and their IDE."""

from django.db.models import Case, F, IntegerField, Max, Value, When

from judge.models import Submission
from judge.utils.hidden_results import exclude_hidden_result_submissions


def mark_navigation_states(entries, profile, viewer, contest=None, participation=None):
    states = {}
    if profile is not None and entries:
        submissions = Submission.objects.filter(
            user=profile, problem_id__in=[entry["problem"].id for entry in entries]
        ).exclude(status__in=Submission.IN_PROGRESS_GRADING_STATUS)
        if contest is not None:
            submissions = submissions.filter(contest_object=contest)
            if participation is not None:
                submissions = submissions.filter(contest__participation=participation)
        submissions = exclude_hidden_result_submissions(submissions, viewer)
        states = dict(
            submissions.values("problem_id")
            .annotate(
                state=Max(
                    Case(
                        When(
                            status="D",
                            result="AC",
                            is_pretested=False,
                            case_total__gt=0,
                            case_points__gte=F("case_total"),
                            then=Value(3),
                        ),
                        When(status="D", case_points__gt=0, then=Value(2)),
                        default=Value(1),
                        output_field=IntegerField(),
                    )
                )
            )
            .values_list("problem_id", "state")
        )
    for entry in entries:
        entry["state"] = (
            "hidden"
            if entry.get("is_result_hidden")
            else {0: "new", 1: "bad", 2: "partial", 3: "ok"}[
                states.get(entry["problem"].id, 0)
            ]
        )
    return entries
