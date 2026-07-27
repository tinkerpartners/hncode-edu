# Issues

Known trade-offs and deferred work in this fork, each mirrored by a GitHub issue on
[tinkerpartners/hnedu](https://github.com/tinkerpartners/hnedu/issues). This file is for the ones a
reader of the *code* needs to know about — a measurement, a shortcut taken deliberately, a thing
that will bite at a larger size. Bugs and feature requests live in the issue tracker alone.

Sibling docs: [`PATCHES.md`](PATCHES.md) (local patches vs upstream) and
[`deploy/README.md`](deploy/README.md) (deploy + upstream-sync runbook).

---

## #39 — Course list: per-course progress costs ~31 extra queries and ~3x page time

**Where:** `judge/views/course.py`, `bulk_calculate_courses_total_progress()` →
`CourseList.get_context_data`. Introduced by PR #38.

**The trade-off.** The card total is produced by *composing* the existing
`bulk_calculate_lessons_progress` / `bulk_calculate_contests_progress` / `calculate_total_progress`
helpers, so the `/courses/` card and the `/course/<slug>` page can never disagree. The cost of that
guarantee is a handful of queries per course on the page, because those helpers are shaped
per-course.

**Measured** on green against the production DB — busiest student (`xuannguu`, 18 courses),
`/courses/?tab=my`, page size 9, warm caches, Django test client:

| | queries | time |
|---|---|---|
| before PR #38 | 9 | 0.08s |
| after PR #38 | 40 | 0.22–0.28s |
| after PR #38, first commit only | 40 | 0.69–0.85s |

The second commit of that PR already removed the dominant cost: `lesson.get_problems()` was
inflating ~1900 `Problem` instances (0.45s) that only `problem__in` ever read. What remains, over
the same 9 courses / 1859 problems:

| step | queries | time |
|---|---|---|
| gather lessons + problem ids | 9 | 0.02s |
| `bulk_max_case_points_per_problem` — one `BestSubmission` lookup for the whole page | 2 | 0.04s |
| per-course lesson + contest progress | 19 | 0.12s |

**Why it matters:** the remainder is **linear in courses per page** (~3.4 queries each:
`get_lessons()`, `get_contests()`, and the `ContestParticipation` / `ContestProblem` lookups inside
`bulk_calculate_contests_progress`). Page size is 9, so it is bounded today — but it scales the
wrong way, and the worst case is a student whose whole page carries contests.

**Ways out**, cheapest first — see [#39](https://github.com/tinkerpartners/hnedu/issues/39) for the
detail. Batching the lesson/contest *fetches* across the page is free of risk and saves ~18
queries. Batching the contest *scoring* lookups saves ~18 more but moves the per-course weighting
out of `bulk_calculate_contests_progress`, so it needs a shared helper rather than a second copy of
the formula. Caching the per-`(user, course)` total helps repeat loads only. Reading a stored total
from the `CourseLessonProgress` / `recalculate_course_grades` machinery is fastest but rides on
`needs_progress_recalculation` for freshness — it could put a stale number on the card, which is the
one property PR #38 exists to prevent.

**Not a blocker.** The page is ~0.25s and correct; this is recorded so the next person to touch
`CourseList` knows the shape of the cost and does not "discover" it as a regression.
