# Local patches against `LQDJudge/online-judge`

This repository is **not** a GitHub fork. It is a standalone repo carrying the full upstream
history plus the HNCode patches on top. Upstream is a second git remote:

```
origin    git@github.com:tinkerpartners/hnedu.git      # this repo; branch `main` = deployed
upstream  https://github.com/LQDJudge/online-judge.git # branch `master`; never checked out
```

Every entry below is a **deliberate divergence that must survive an upstream merge**. When
`git merge upstream/master` conflicts in one of these files, keep our side of the change
described here and take upstream's side everywhere else.

`main` is protected, so a sync lands via pull request — and **must be merged with a merge
commit, never squashed or rebased**, or the merge base recorded against `upstream/master` is
lost and the next sync conflicts across the whole tree. See `deploy/README.md`.

Base commit: `dbd4d360` ("Prevent Unicode username spoofing").

---

## 1. Course-enrolled access to private problems

**Files:** `judge/models/problem.py` (`Problem.is_accessible_by`)

Upstream grants problem access only to public problems, editors, testers, and users inside a
contest containing the problem. The AlgoMaster fork additionally grants access when the user
is enrolled in a course whose lesson contains the problem. Without this, **every** private
(`is_public=0`) problem assigned through a course lesson 404s for the students it was
assigned to.

We use `Course.get_accessible_courses(user.profile)`, which upstream already provides. The
fork also had a `requires_giftcode`/`CourseGiftcode` branch; that model does not exist
upstream and is intentionally **not** ported.

**Conflict risk:** medium — any upstream refactor of `is_accessible_by` collides.

## 2. PDF statements served from problem data storage (EFS)

**Files:** `judge/models/problem.py` (`pdf_description` field),
`judge/views/problem.py` (`ProblemPdfDescriptionView.get`),
migration `0262_alter_contest_format_name_and_more`

All 2680 problems with a `pdf_description` store it in fork format `<code>/<file>`, and the
file physically lives on EFS at `/mnt/efs/problems/<code>/`, never in media. Upstream
declares the field against default media storage and serves it with `serve_file_inline`,
which redirects to `storage.base_url` (= `MEDIA_URL`) and 404s.

We declare `storage=problem_data_storage, upload_to=problem_directory_file` and read the file
directly from its storage rather than redirecting.

**Conflict risk:** medium — watch for upstream changing `serve_file_inline` or the field.

## 3. AMOJ and VNOJ contest formats

**Files:** `judge/contest_format/amoj.py`, `judge/contest_format/vnoj.py` (both new),
`judge/contest_format/__init__.py` (two import lines)

HNCode contests carried over from the fork use the `amoj` and `vnoj` format modules, which
upstream does not ship. Without them every grading-end raises `KeyError` on the
`contest_format.formats` lookup, which silently leaves contest scores at 0.

**Conflict risk:** low — but `__init__.py` is a short file upstream edits whenever it adds a
format, so expect the occasional trivial conflict on the import block.

## 4. Underscores allowed in codes and slugs

**Files:** `judge/models/{problem,contest,quiz,course}.py`, `judge/forms.py`
(`ProblemCloneForm`, `ContestCloneForm`), `judge/views/quiz_import.py`,
`templates/quiz/import.html`, migrations `0261`, `0263`, `0264`, `0265`

Migrated content uses underscores in identifiers (e.g. contest key `26c104_kiemtra01`), which
upstream rejects with `^[a-z0-9]+$`. We relax to `^[a-z0-9_]+$` (and `^[-a-zA-Z0-9_]+$` for
course slugs). `Problem.code` is also widened from 30 to 50 characters.

**This one is load-bearing: reverting it makes existing rows fail validation.**

**Conflict risk:** low — but the change spans 7 files, so grep for `a-z0-9` after any merge to
confirm none reverted.

## 5. `ContestForm.clean` add-form crash

**Files:** `judge/admin/contest.py`

`clean()` unconditionally ran
`cleaned_data["banned_users"].filter(current_contest__contest=self.instance)`. On an *add*,
`self.instance` has no pk, so the related filter raises
`ValueError: Model instances passed to related filters must be saved`. It also never returned
`cleaned_data` and indexed it directly (`KeyError` if the field fails validation).

**This is a genuine upstream bug, not an HNCode-specific need.** It is still present in
upstream master and is a **good candidate to submit upstream as a PR**, which would
permanently remove this entry from the list.

**Conflict risk:** low.

## 6. `/about/` as an editable flatpage

**Files:** `dmoj/urls.py`

`/about/` was a hardcoded view plus template showing stale LQDOJ boilerplate, so editing it
needed a code deploy. The route now points at `django.contrib.flatpages.views.flatpage` while
keeping the URL name `about` (the nav tab resolves `url("about")`). Content is edited in the
admin under Flat Pages.

The old `judge/views/about.py` and `templates/about/about.html` are now dead for this route
and are left untouched, so upstream changes to them merge harmlessly.

**Conflict risk:** low — but `dmoj/urls.py` is large and frequently edited upstream.

## 7. Branding

**Files:** `resources/icons/*`, `templates/base.html`, `templates/site-logo-fragment.html`,
`templates/home.html`, `templates/task_status.html`, `templates/recent-organization.html`,
`templates/chat/{online_status,user_online_status}.html`,
`templates/home/feed-{courses,groups}-card.html`, `templates/organization/list.html`

HNCode logo, favicon/PWA icon set, manifests and default group avatar replace the LQDOJ
originals. `base.html` also links the sized apple-touch and mstile icons that upstream
references but never shipped.

**Cloudflare edge-caches `/static/` for 4 hours and no purge token exists on the droplet**, so
asset references carry `?v=` cache-busting tokens. Current tokens: favicons `20260719e`,
`icons/icon.svg` `20260719d`, nav logo `20260719`. **Bump the token whenever an asset is
swapped**, or the edge keeps serving the old bytes.

**Conflict risk:** high on `templates/base.html` (upstream edits `<head>` often). Binary icon
conflicts always resolve to "keep ours".

## 8. `course.css` in the style build

**Files:** `make_style.sh`

`course.css` compiled to `sass_processed/` but was missing from the postcss `FILES` list, so it
never reached `resources/` and `collectstatic` never saw it — `/static/course.css` 404d.

**Conflict risk:** low.

## 9. `BestSubmission` ranks by test-case ratio, not normalized points

**Files:** `judge/models/submission.py` (`best_submission_order_annotations`,
`BEST_SUBMISSION_ORDER`, `BestSubmission.recalculate_for_user_problem`, `BestSubmission.save`),
`judge/views/course.py` (`bulk_max_case_points_per_problem` hidden-result fallback)

Upstream picks the best submission for a (user, problem) pair with
`.order_by("-points", "-date")`. `Submission.points` is the **normalized problem score**
(`case_points / case_total * problem.points` when partial, `problem.points` on AC else `0`), but
every reader of `judge_bestsubmission` consumes the **test-case ratio**: lesson grades render
`points / case_total * lesson_problem.score` and `user_completed_ids` tests
`points >= case_total`. Ranking by one metric and reading another diverges in two ways:

- **Editing `Problem.points` after judging** leaves older rows carrying the old normalized score.
  HNCode bulk-changed 632 problems from `points=100` to `points=1`, after which a `3/10` WA judged
  before the change (`points=30`) outranked a `10/10` AC judged after it (`points=1`) — solved
  problems rendered as 30%.
- **On a non-partial problem** every non-AC submission has `points=0`, so `-points, -date`
  collapses to "the most recent submission" and a student's grade **drops when they resubmit
  something worse**.

689 of 179,055 rows were wrong when this was found; 685 of them sat inside course lessons.

We order by `-case_ratio, -has_cases, -points, -date, -id`, where `case_ratio` is
`case_points / case_total` guarded by `CASE WHEN case_total > 0` so a zero-case row never divides
by zero and never displaces a graded run (`case_total=0` is filtered out downstream, which would
erase the pair from the grades page). Ratio ordering **still defends against rescaled test data**,
which is why upstream reached for `-points`: after `case_total` goes `1000 -> 12`, an un-rejudged
WA at `750/1000 = 0.75` still loses to a fresh AC at `12/12 = 1.00`, because both are compared as
fractions of their own scale. `-points` survives as a tie-break so a true AC beats a
100%-of-cases non-AC on a non-partial problem.

`judge/views/course.py` carries a **second copy** of the same ranking, used when a student's best
submission belongs to a contest with hidden results; both call sites share
`best_submission_order_annotations()` / `BEST_SUBMISSION_ORDER` so they cannot drift apart.
`BestSubmission.save()` also had to start watching `case_total` — it only fired the lesson-grade
trigger on a `points` change, so a best submission moving to a differently-scaled run changed the
grade silently. Regression tests live in
`judge/tests/test_course_prerequisites.py::BestSubmissionModelTest`.

**This is a genuine upstream bug, not an HNCode-specific need.** It is still present in upstream
master and is a **good candidate to submit upstream as a PR**, which would permanently remove this
entry from the list — like patch 5.

**Conflict risk:** low on `judge/models/submission.py` — upstream rarely touches
`recalculate_for_user_problem`. Medium on `judge/views/course.py`, which is large and edited
upstream; the conflict, if any, is the one `order_by` call.

## 10. `websocket/config.js` is untracked

**Files:** `.gitignore`, `deploy/websocket-config.js.example`, and the *removal* of
`websocket/config.js` from the index

Upstream tracks `websocket/config.js` with the placeholder token `backend_auth_token: 'lqdoj'`.
The live file on the server carries the real `EVENT_DAEMON_KEY`, so for months it sat as an
**uncommitted working-tree edit** — the last un-version-controlled divergence on the droplet,
and one that a stray `git checkout .` would have silently replaced with the placeholder.

Committing the real value is not an option: **this repository is public.** So the file follows
the same convention as `dmoj/local_settings.py` — gitignored, with a `CHANGEME` template in
`deploy/`. The token was verified never to have been committed (`git log --all -S<token>` is
empty), so it did **not** need rotating.

`backend_auth_token` must equal `EVENT_DAEMON_KEY` in `dmoj/local_settings.py`. When they
disagree the site cannot authenticate to the event daemon and every live update — submission
status, contest scoreboard, chat — stops arriving, with no error on the page.

Deploying this entry needs a one-time backup/restore of the live file, because the commit
deletes a file the server has modified; see `deploy/README.md`.

**Conflict risk:** low — but an upstream merge that edits `websocket/config.js` will report it
as deleted-by-us. Keep our side (deleted) and port any upstream change into
`deploy/websocket-config.js.example` and the live file by hand.

## 11. "All" tabs on the course and group lists

**Files:** `judge/models/course.py` (`Course.get_visible_courses`),
`judge/views/course.py` (`CourseList.get_queryset`),
`judge/views/organization.py` (`OrganizationList.get_queryset`),
`templates/course/list_left_sidebar.html`, `templates/course/list.html`,
`templates/organization/list.html`, `locale/vi/LC_MESSAGES/django.po`

Upstream's course sidebar offers only "My Courses" / "Join Courses", and its group list only
Communities / Mine / Public / Private / Blocked — there is no way to browse everything at
once. We add a `?tab=all` to both lists and link both from the courses sidebar ("All Courses",
"All Groups").

`Course.get_visible_courses(profile)` is ours: public non-org courses, public courses of the
viewer's organizations, and the courses they are enrolled in; everything for superusers.
Private and org-scoped courses stay hidden from outsiders — do not "simplify" it to
`Course.objects.all()`.

Both list templates dispatch on `current_tab` per block, so the tab needs a render block **and**
— for groups — a branch in the `org_list` macro, otherwise the fallback branch (written for
`blocked`) shows an "Unblock" button on every group.

**Conflict risk:** low for the model/view hunks; medium in `templates/organization/list.html`,
which upstream edits fairly often.

## 12. Course list as a card grid

**Files:** `resources/course.scss` (`.course-list-page`), `judge/views/course.py`
(`CourseList.paginate_by`), the nine templates linking `course.css`

Upstream renders `/courses/` as a single column of wide horizontal rows, next to a groups list
that is a 3-across card grid. We restyle `.course-list-page` to match `.organization-container`:
three cards per row at ≥800px, two below, one below 480px — deliberately the same breakpoints
as `.organization-card`, so changing one list's grid means changing the other's too.

`paginate_by` is **9**, not upstream's 10, so a desktop page is a full 3×3.

`course.css` is generated (`make_style.sh` → gitignored `resources/course.css`) and is **not**
content-hashed, while Cloudflare caches `/static/` for 4 h. The `<link>` tags therefore carry a
`?v=` token — currently `20260727c`. **Bump it whenever `course.scss` changes**, or the edit is
invisible behind the edge cache for four hours.

The card chrome is copied from `.organization-card` on purpose — white card, 1px `#ddd`
border, 8px radius, `0 2px 4px` shadow, 1em padding, `translateY(-5px)` on hover, cover on
`#f0f0f0`. The one deliberate difference: `.organization-card` stacks its contents
(`flex-direction: column`, centred text), while `.course-item` lays them out in a **row** —
100px square cover on the left, text left-aligned beside it — by operator preference. The
3-across wrapping is on `.course-list`, so the two are independent: changing the card's
`flex-direction` does not change how many cards fit per row.

**Trap:** the generic `.course-list .course-item` / `.course-image` block at the top of
`course.scss` matches the same elements at **equal specificity**, so only source order decides
and every property it sets must be restated in `.course-list-page`. The first cut of this patch
missed `margin-right: 20px` on `.course-image`, which pushed the full-width cover 20px past the
card's right edge (and `margin-bottom`/`box-shadow` on the card leaked the same way).

**Conflict risk:** medium in `course.scss` — the hunk rewrites a block upstream also touches.

---

## Not patches (recorded so they are not "fixed" again)

- **Admin add-form 403s** on `submission`, `auth/user`, `profile` and `admin/logentry` are **by
  design** — `has_add_permission` returns False (e.g. `judge/admin/submission.py`).
- **`score=0` on a contest participation is usually correct.** 504 of the 507 such rows are
  *virtual* participations, and every contest format clamps scoring to
  `sub.date < contest.end_time`; a virtual run happens after the contest ends, so nothing
  scores. Blue (the previous production) shows the same zeros. Check `virtual` first and diff
  against the old production before "fixing" historical contests.
- **`BestSubmission` / `BestQuizAttempt` are upstream-only tables** with no counterpart in the
  fork, so no data migration can populate them. They must be **rebuilt from `judge_submission`
  after any data load**, or every course grade and problem-completion state reads 0. This is
  data, not code — it is recorded here because the symptom looks exactly like a code bug.

  **Rebuild for every judged `(user, problem)` pair — never for a filtered subset.** The rows
  are only ever written by `finished_submission()` at judge time, so *migrated* submissions
  never produce one. A scope-limited backfill therefore leaves a permanent hole: any pair that
  becomes relevant **later** — a lesson created, a problem added to a lesson, a student
  enrolled — has no row and silently reads 0, because the submissions it would be built from
  were judged long before and will never be re-judged.

  This is not hypothetical. The July 2026 migration backfilled only
  `problem_id IN (courselessonproblem) AND user_id IN (courserole)` *as of that date*. A week
  later **26,438 pairs had no row at all** — 920 of them inside course lessons, where students
  with a perfect AC were shown 0. The rest silently broke the solved/attempted markers on the
  problem list, since `user_completed_ids` reads the same table.

  Verify with the query below; it must return 0. A `JOIN` against `judge_bestsubmission` is
  **not** a valid check — a missing row cannot appear on either side of an inner join, so a
  join-based query reports "all clean" precisely when rows are absent. Use a `LEFT JOIN … IS
  NULL`:

  ```sql
  SELECT COUNT(*) FROM (
    SELECT DISTINCT s.user_id, s.problem_id
    FROM judge_submission s
    LEFT JOIN judge_bestsubmission b
      ON b.user_id = s.user_id AND b.problem_id = s.problem_id
    WHERE s.status = 'D' AND s.case_total > 0 AND b.id IS NULL
  ) t;
  ```

  Repair by calling `BestSubmission.recalculate_for_user_problem(user_id, problem_id)` for each
  missing pair rather than writing SQL — it is the same code path the judge uses, so the result
  cannot drift from it, and `BestSubmission.save()` refreshes the affected lesson grades on the
  way through. Budget roughly 20 minutes per 25k pairs. Creating a missing row can only raise a
  grade or turn an icon green, never the reverse, so the operation is safe to run on a live site;
  follow it by dirtying `user_completed_ids` / `user_attempted_ids` for the affected profiles.

## Operational rule

`green-bridged` and `green-celery` are long-lived Python processes that load code once at
start. **After any deploy, restart all three services, not just the site:**

```
supervisorctl restart green-site green-bridged green-celery
```

Restarting uwsgi alone silently leaves the bridge and worker on stale code, and the failure
then surfaces far from its cause. In July 2026 a stale `green-bridged` ran pre-patch code for
two days: every contest score stayed 0 while the web process behaved perfectly.

**Never change `judge_problem.points` with direct SQL.** It bypasses every ORM hook — no admin
log, no reversion record, no rescore of the already-judged submissions, no `BestSubmission`
rebuild. A bulk `100 -> 1` re-pointing done this way is what surfaced patch 9, and it left
`Submission.points` stale on 632 problems (which still feeds contest scoring, performance points
and the problem-list score column). Re-point through the admin or a `manage.py` command, and
follow it with a rejudge.
