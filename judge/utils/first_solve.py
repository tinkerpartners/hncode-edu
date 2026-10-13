"""Who solved each contest problem first (A15, #103).

Computed on the server for the whole contest, so the mark does not depend on
which page or filter of the ranking is on screen. Owner decisions
(2026-10-13):

  * "Solved" means the submission's current verdict is AC.
  * Current state: a rejudge that takes the AC away moves the mark.
  * "First" is by submission time; ties go to the lower submission id.

Only live, non-disqualified participations count (no virtual, no spectators),
and only what the viewer's ranking itself is allowed to show:

  * with a freeze and outside the final ranking, only submissions made
    before the participant's freeze time;
  * no mark on problems whose results are hidden from the viewer, or whose
    subtasks the format hides, outside the final ranking.

Quiz rows have no AC, so they never get a mark.
"""

from judge.models import ContestParticipation, ContestSubmission
from judge.utils.hidden_results import hidden_result_contest_problem_ids


def get_first_solves(contest, user, show_final=False):
    """{contest_problem_id: participation_id} of the first AC per problem."""
    excluded = set(hidden_result_contest_problem_ids(contest, user))
    if not show_final and hasattr(contest.format, "get_hidden_subtasks"):
        excluded |= {
            int(problem_id)
            for problem_id, subtasks in contest.format.get_hidden_subtasks().items()
            if subtasks
        }

    freeze = contest.freeze_after if not show_final else None
    rows = (
        ContestSubmission.objects.filter(
            problem__contest=contest,
            problem__problem__isnull=False,
            participation__virtual=ContestParticipation.LIVE,
            participation__is_disqualified=False,
            submission__result="AC",
            # In contest time only (a rejudged or imported row cannot sneak in).
            submission__date__gte=contest.start_time,
            submission__date__lt=contest.end_time,
        )
        .exclude(problem_id__in=excluded)
        .order_by("problem_id", "submission__date", "submission_id")
        .values_list(
            "problem_id",
            "participation_id",
            "submission__date",
            "participation__real_start",
        )
    )

    first = {}
    for problem_id, participation_id, date, real_start in rows.iterator():
        if problem_id in first:
            continue
        if freeze:
            # ContestParticipation.start for a live participant.
            start = contest.start_time if contest.time_limit is None else real_start
            if date >= start + freeze:
                continue
        first[problem_id] = participation_id
    return first


def get_first_solve_urls(contest, user, show_final=False):
    """The ranking-cell popup URLs to mark, as the format renders them."""
    first = get_first_solves(contest, user, show_final)
    if not first:
        return []
    problems = {
        cp.id: cp
        for cp in contest.contest_problems.filter(id__in=first).select_related(
            "problem"
        )
    }
    participations = ContestParticipation.objects.in_bulk(first.values())
    return sorted(
        contest.format.get_submission_url(participations[pid], problems[cpid])
        for cpid, pid in first.items()
        if cpid in problems and pid in participations
    )
