from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import connection
from django.template.defaultfilters import floatformat
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy

from judge.contest_format.default import DefaultContestFormat
from judge.contest_format.registry import register_contest_format
from judge.timezone import from_database_time, to_database_time
from judge.utils.timedelta import nice_repr


@register_contest_format("amoj")
class AMOJContestFormat(DefaultContestFormat):
    name = gettext_lazy("AMOJ")
    config_defaults = {"cumtime": True, "penalty": 5, "scoreRule": "standard"}
    config_validators = {
        "cumtime": lambda x: True,
        "penalty": lambda x: x >= 0,
        "scoreRule": lambda s: str(s).lower() in {"standard", "ioi", "ultimate"},
    }
    """
        cumtime: Specify True if cumulative time is to be used in breaking ties. Defaults to True.
        penalty: Number of penalty minutes each incorrect submission adds. Default to 5.
        scoreRule: The scoring rule to use. Can be "standard", "ioi" or "ultimate".
            standard: The score of a problem is the highest score achieved by any submission.
            ioi: The score of a subtask is the highest score achieved by any submission for that subtask.
                 The score of a problem is the sum of the scores of all subtasks in that problem.
            ultimate: The score of a problem is the score of the last submission for that problem.
    """

    @classmethod
    def validate(cls, config):
        if config is None:
            return

        if not isinstance(config, dict):
            raise ValidationError("AMOJ-styled contest expects no config or dict as config")

        for key, value in config.items():
            if key not in cls.config_defaults:
                raise ValidationError('unknown config key "%s"' % key)
            if not isinstance(value, type(cls.config_defaults[key])):
                raise ValidationError('invalid type for config key "%s"' % key)
            if not cls.config_validators[key](value):
                raise ValidationError('invalid value "%s" for config key "%s"' % (value, key))

    def __init__(self, contest, config):
        self.config = self.config_defaults.copy()
        self.config.update(config or {})
        self.contest = contest

    def get_hidden_subtasks(self):
        queryset = self.contest.contest_problems.values_list("id", "hidden_subtasks")
        res = {}
        for problem_id, hidden_subtasks in queryset:
            subtasks = set()
            if hidden_subtasks:
                hidden_subtasks = hidden_subtasks.split(",")
                for i in hidden_subtasks:
                    try:
                        subtasks.add(int(i))
                    except Exception:
                        pass
                res[str(problem_id)] = subtasks
        return res

    def _frozen_time(self, participation, include_frozen=False):
        frozen_time = self.contest.end_time
        if self.contest.freeze_after and not include_frozen:
            frozen_time = participation.start + self.contest.freeze_after
        return frozen_time

    # IOI's utility function
    def get_results_by_subtask(self, participation, include_frozen=False):
        frozen_time = self._frozen_time(participation, include_frozen)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT q.prob,
                       q.prob_points,
                       MIN(q.date) as `date`,
                       q.batch_points,
                       q.total_batch_points,
                       q.batch,
                       q.subid
                FROM (
                         SELECT cp.id          as `prob`,
                                cp.points      as `prob_points`,
                                sub.id         as `subid`,
                                sub.date       as `date`,
                                tc.points      as `points`,
                                tc.batch       as `batch`,
                                SUM(tc.points) as `batch_points`,
                                SUM(tc.total)  as `total_batch_points`
                         FROM judge_contestproblem cp
                                  INNER JOIN
                              judge_contestsubmission cs
                              ON (cs.problem_id = cp.id AND cs.participation_id = %s)
                                  LEFT OUTER JOIN
                              judge_submission sub
                              ON (sub.id = cs.submission_id AND sub.status = 'D')
                                  INNER JOIN judge_submissiontestcase tc
                              ON sub.id = tc.submission_id
                         WHERE sub.date < %s
                         GROUP BY cp.id, tc.batch, sub.id
                     ) q
                         INNER JOIN (
                    SELECT prob, batch, MAX(r.batch_points) as max_batch_points
                    FROM (
                             SELECT cp.id          as `prob`,
                                    tc.batch       as `batch`,
                                    SUM(tc.points) as `batch_points`
                             FROM judge_contestproblem cp
                                      INNER JOIN
                                  judge_contestsubmission cs
                                  ON (cs.problem_id = cp.id AND cs.participation_id = %s)
                                      LEFT OUTER JOIN
                                  judge_submission sub
                                  ON (sub.id = cs.submission_id AND sub.status = 'D')
                                      INNER JOIN judge_submissiontestcase tc
                                  ON sub.id = tc.submission_id
                             WHERE sub.date < %s
                             GROUP BY cp.id, tc.batch, sub.id
                         ) r
                    GROUP BY prob, batch
                ) p
                ON p.prob = q.prob AND (p.batch = q.batch OR p.batch is NULL AND q.batch is NULL)
                WHERE p.max_batch_points = q.batch_points
                GROUP BY q.prob, q.batch
                """,
                (
                    participation.id,
                    to_database_time(frozen_time),
                    participation.id,
                    to_database_time(frozen_time),
                ),
            )

            return cursor.fetchall()

    # Get score rule from configs
    def get_score_rule(self) -> str:
        value = str(self.config.get("scoreRule", "standard")).lower()
        return value if value in {"standard", "ioi", "ultimate"} else "standard"

    # def count_incorrect_until(self, participation, prob_id, cutoff_dt, include_frozen=False):
    #     frozen_time = self._frozen_time(participation, include_frozen=False)
    #     if cutoff_dt is None:
    #         return 0
    #     # Nếu đang frozen thì không tính submission sau freeze:
    #     cutoff = cutoff_dt
    #     if include_frozen:
    #         cutoff = min(cutoff, frozen_time)

    #     with connection.cursor() as cursor:
    #         cursor.execute(
    #         """
    #             SELECT COUNT(DISTINCT sub.id)
    #             FROM judge_contestsubmission cs
    #             INNER JOIN judge_submission sub
    #                 ON sub.id = cs.submission_id AND sub.status = 'D'
    #             WHERE cs.participation_id = %s AND cs.problem_id = %s AND sub.date <= %s AND sub.result IS NOT NULL AND sub.result NOT IN ('IE', 'CE')
    #         """,
    #         (participation.id, prob_id, to_database_time(cutoff))
    #         )
    #         cnt = cursor.fetchone()[0] or 0
    #     return max(0, cnt - 1)

    # Standard: highest submission score; earliest time achieved.
    def scores_standard(self, participation):
        cumtime = 0
        points = 0
        penalty = 0
        format_data = {}

        frozen_time = self._frozen_time(participation, include_frozen=True)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    MAX(cs.points) as `score`,
                    (
                        SELECT MIN(csub.date)
                        FROM judge_contestsubmission ccs
                        LEFT JOIN judge_submission csub ON (csub.id = ccs.submission_id)
                        WHERE ccs.problem_id = cp.id AND ccs.participation_id = %s AND ccs.points = MAX(cs.points)
                    ) AS `time`,
                    cp.id AS `prob`
                FROM judge_contestproblem cp
                INNER JOIN judge_contestsubmission cs
                    ON (cs.problem_id = cp.id AND cs.participation_id = %s)
                LEFT OUTER JOIN judge_submission sub
                    ON (sub.id = cs.submission_id)
                WHERE sub.date < %s
                GROUP BY cp.id
                """,
                (participation.id, participation.id, to_database_time(frozen_time)),
            )

            # Build result per problem
            for score, time, prob in cursor.fetchall():
                time = from_database_time(time)
                if self.config["cumtime"]:
                    dt = (time - participation.start).total_seconds()

                    # Compute penalty
                    if self.config["penalty"]:
                        # An IE can have a submission result of `None`
                        subs = (
                            participation.submissions.exclude(submissions__result__isnull=True)
                            .exclude(submission__result__in=["IE", "CE"])
                            .filter(problem_id=prob)
                        )
                        if score:
                            prev = subs.filter(submission__date__lte=time).count() - 1
                        else:
                            # We should always display the penalty, even if the user has a score of 0
                            prev = subs.count()
                    else:
                        prev = 0

                    if score:
                        cumtime = cumtime + dt
                        penalty += prev * self.config["penalty"] * 60

                format_data[str(prob)] = {
                    "time": dt,
                    "points": score,
                    "penalty": prev,
                }
                points += score

        self.handle_frozen_state(participation, format_data)
        participation.cumtime = max(cumtime, 0) + max(penalty, 0)
        participation.score = round(points, self.contest.points_precision)
        participation.tiebreaker = 0
        participation.format_data = format_data
        participation.save()

    # IOI: best per subtask, then sum across subtasks -> per-problem(score, time_reached)
    def scores_ioi(self, participation):
        hidden_subtasks = self.get_hidden_subtasks()

        def calculate_format_data(participation, include_frozen):
            format_data = {}
            for (
                problem_id,
                problem_points,
                time,
                subtask_points,
                total_subtask_points,
                subtask,
                sub_id,
            ) in self.get_results_by_subtask(participation, include_frozen):
                problem_id = str(problem_id)
                time = from_database_time(time)
                if self.config["cumtime"]:
                    dt = (time - participation.start).total_seconds()
                else:
                    dt = 0
                if format_data.get(problem_id) is None:
                    format_data[problem_id] = {
                        "points": 0,
                        "time": 0,
                        "penalty": 0,
                        "total_points": 0,
                        "problem_points": 0,
                    }
                if subtask not in hidden_subtasks.get(problem_id, set()) or include_frozen:
                    format_data[problem_id]["points"] += subtask_points
                format_data[problem_id]["total_points"] += total_subtask_points
                format_data[problem_id]["time"] = max(dt, format_data[problem_id]["time"])
                format_data[problem_id]["problem_points"] = problem_points
                if self.config["cumtime"] and self.config["penalty"]:
                    # An IE can have a submission result of `None`
                    subs = (
                        participation.submissions.exclude(submission__result__isnull=True)
                        .exclude(submission__result__in=["IE", "CE"])
                        .filter(problem_id=problem_id)
                    )
                    if subtask_points:
                        prev = subs.filter(submission__date__lte=time).count() - 1
                    else:
                        # We should always display the penalty, even if the user has a score of 0
                        prev = subs.count()
                else:
                    prev = 0
                format_data[problem_id]["penalty"] = max(format_data[problem_id]["penalty"], prev * self.config["penalty"] * 60)
            return format_data

        def recalculate_results(format_data):
            cumtime = 0
            score = 0
            penalty = 0
            for problem_data in format_data.values():
                if not problem_data["total_points"]:
                    continue
                time = problem_data["time"]
                thisPenalty = problem_data["penalty"]
                problem_data["points"] = problem_data["points"] / problem_data["total_points"] * problem_data["problem_points"]
                if self.config["cumtime"] and problem_data["points"]:
                    cumtime += time
                    if self.config["penalty"]:
                        penalty += thisPenalty
                score += problem_data["points"]
            return score, cumtime, penalty

        format_data = calculate_format_data(participation, False)
        score, cumtime, penalty = recalculate_results(format_data)
        self.handle_frozen_state(participation, format_data)
        participation.cumtime = max(cumtime, 0) + max(penalty, 0)
        participation.score = round(score, self.contest.points_precision)
        participation.tiebreaker = 0
        participation.format_data = format_data

        format_data_final = calculate_format_data(participation, True)
        score_final, cumtime_final, penalty_final = recalculate_results(format_data_final)
        participation.cumtime_final = max(cumtime_final, 0) + max(penalty_final, 0)
        participation.score_final = round(score_final, self.contest.points_precision)
        participation.format_data_final = format_data_final
        participation.save()

    # Ultimate: Last submission only
    def scores_ultimate(self, participation):
        cumtime = 0
        points = 0
        penalty = 0
        format_data = {}

        frozen_time = self._frozen_time(participation, include_frozen=True)

        with connection.cursor() as cursor:
            # ROW_NUMBER() OVER (PARTITION BY cp.id ORDER BY sub.date DESC) AS
            cursor.execute(
                """
                SELECT last_sub.points AS `score`,
                       last_sub.date AS `time`,
                       last_sub.prob AS `prob`
                FROM (
                    SELECT cp.id AS prob,
                           cs.points AS points,
                           sub.date AS date,
                           ROW_NUMBER() OVER (PARTITION BY cp.id ORDER BY sub.date DESC) AS rn
                    FROM judge_contestproblem cp
                    INNER JOIN judge_contestsubmission cs
                        ON (cs.problem_id = cp.id AND cs.participation_id = %s)
                    INNER JOIN judge_submission sub
                        ON (sub.id = cs.submission_id AND sub.status = 'D')
                    WHERE sub.date < %s AND sub.result is NOT NULL AND sub.result NOT IN ('IE', 'CE')
                ) last_sub
                WHERE last_sub.rn = 1
                """,
                (participation.id, to_database_time(frozen_time)),
            )

            # Build result per problem
            for score, time, prob in cursor.fetchall():
                time = from_database_time(time)
                if self.config["cumtime"]:
                    dt = (time - participation.start).total_seconds()

                    # Compute penalty
                    # All submission before this last submission is considered as a fail submission
                    if self.config["penalty"]:
                        subs = (
                            participation.submissions.exclude(submission__result__isnull=True)
                            .exclude(submission__result__in=["IE", "CE"])
                            .filter(problem_id=prob, submission__date__lt=time)
                        )
                        if score:
                            prev = subs.filter(submission__date__lte=time).count() - 1
                        else:
                            prev = subs.count()
                    else:
                        prev = 0

                    if score:
                        cumtime = cumtime + dt
                        penalty += prev * self.config["penalty"] * 60

                format_data[str(prob)] = {
                    "time": dt,
                    "points": score,
                    "penalty": prev,
                }
                points += score

        self.handle_frozen_state(participation, format_data)
        participation.cumtime = max(cumtime, 0) + max(penalty, 0)
        participation.score = round(points, self.contest.points_precision)
        participation.tiebreaker = 0
        participation.format_data = format_data
        participation.save()

    def update_participation(self, participation):
        thisScoreRule = self.get_score_rule()
        if thisScoreRule == "standard":
            return self.scores_standard(participation)
        elif thisScoreRule == "ioi":
            return self.scores_ioi(participation)
        elif thisScoreRule == "ultimate":
            return self.scores_ultimate(participation)
        else:
            raise NotImplementedError("Unknown score rule")

    def display_user_problem(self, participation, contest_problem, show_final=False):
        # if show_final:
        #     format_data = (participation.format_data_final or {}).get(str(contest_problem.id))
        # else:
        format_data = (participation.format_data or {}).get(str(contest_problem.id))
        if format_data:
            penalty = (
                format_html('<small style="color:red;"> ({penalty}) </small>', penalty=floatformat(format_data.get("penalty", 0)))
                if format_data.get("penalty")
                else ""
            )
            return format_html(
                '<td class="{state} problem-score-sol"><a data-featherlight="{url}" href="#">{points}{penalty}<div class="solving-time">{time}</div></a></td>',
                state=(
                    ("pretest-" if self.contest.run_pretests_only and contest_problem.is_pretested else "")
                    + self.best_solution_state(format_data["points"], contest_problem.points)
                    + (" frozen" if format_data.get("frozen") else "")
                ),
                url=reverse(
                    "contest_user_submissions_ajax",
                    args=[
                        self.contest.key,
                        participation.id,
                        contest_problem.problem.code,
                    ],
                ),
                points=floatformat(format_data["points"]),
                penalty=penalty,
                time=nice_repr(timedelta(seconds=format_data["time"]), "noday"),
            )
        else:
            return mark_safe('<td class="problem-score-sol"></td>')
