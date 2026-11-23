# Visibility and permissions for groups and courses (A05, #95)

## Current settings

The edit forms expose two separate fields for both groups and courses.

| Visibility | Who can open it | Joining |
|---|---|---|
| Public | Everyone in scope can find and open the introduction | Join immediately or request approval |
| Private | Members or enrolled students and the resource's managers | Invitation or direct assignment |
| Suspended | The resource's managers and system admins | New requests and invitations are paused |

A public course linked to groups is discoverable by members of its active linked
groups. Opening lessons still requires enrollment. Suspension does not delete
membership, results, pending requests or invitation codes. If every linked group
is suspended, the course is paused for students. The course's teachers and
system admins can still manage it. Existing course permissions do not grant a
group administrator a course role.

The **Members** page of a course shows pending applications to its teachers and
system admins. Approvals recheck the applicant's current group eligibility.
An application never grants access before it is approved. Group approvals
continue to use the existing requests page.

Migration `0290_visibility_and_course_join_requests` adds suspension flags and
the course request table. Existing groups that were closed but visible keep
their URL-only discovery until a manager explicitly chooses **Public**.
Existing public courses with direct enrollment off can accept requests for
approval. Private courses continue to use invitations or direct assignment.

## Legacy rules and compatibility

Three separate questions, never answered by the same flag:

- **Discoverability**: does it appear in lists and search?
- **Access**: may the viewer open its pages?
- **Management**: may the viewer use the management tabs?

For access and management, `Organization.is_open` plays no part: it decides **who may join** without a request. Since #134 it does feed discoverability: outsiders only find groups that are open **and** visible.

## Owner decisions (2026-10-13)

1. "Not publicly visible" means two things for outsiders: hidden from lists and search, and refused at the URL. Members keep full access. There is no "archived" state yet.
2. "Admin" means:
   - a superuser, everywhere;
   - group admins, for their own group;
   - a course's Teacher or Assistant, for that course only.
3. `Organization.is_visible` defaults to `True`, so every existing group behaves as before. Admins switch it off per group, in the group's edit page or in the Django admin.

## Owner decisions (2026-10-15, #134)

4. Outsiders find only groups that are **open and visible**. Members, the group's admins and superusers still find their groups.
5. A **closed but visible** group is *unlisted*: it is missing from every list, search and sitemap for outsiders, but its URL still opens, so an outsider holding the link can request to join. To refuse the URL as well, switch `is_visible` off.
6. An **open but hidden** group stays fully hidden, as in #117: `is_visible = False` always wins over `is_open`.

No flags were changed on production to apply this. Existing closed groups simply stop being listed to outsiders.

| `is_open` | `is_visible` | Outsider: listed / search / sitemap | Outsider: URL, request to join |
|---|---|---|---|
| True | True | yes | yes (joins directly) |
| False | True | **no** | yes |
| True | False | no | 403 |
| False | False | no | 403 |

## Decision functions

Views call these functions and never re-implement the conditions:

| | Group | Course |
|---|---|---|
| Discover (lists, search, sitemap, feed card) | `Organization.is_listed_to(profile)`, `Organization.listed_to(profile, qs)` | `Course.is_visible_to(profile)`, `Course.get_visible_courses(profile)` |
| Access (direct URL) | `Organization.is_visible_to(profile)`, `Organization.visible_to(profile, qs)` | same as above |
| Open the content | Same as above, plus the existing member-only gates on Problems / Contests / Courses / Submissions | `Course.is_accessible_by` (enrolled users or superusers) |
| Manage | `Organization.can_be_managed_by(profile)`, which wraps `Profile.can_edit_organization` | `Course.is_editable_by` (TE/AS or superuser), `CourseAdminMixin` |

The flags are read from the database on every check and never from the organization cache dict. A change of visibility therefore applies on the next request.

## Group matrix: `is_visible = False`

| Entry point | Anonymous | Outsider | Admin of another group | Member | Group admin | Superuser |
|---|---|---|---|---|---|---|
| `/organizations/`, every tab and search | hidden | hidden | hidden | listed | listed | listed |
| Home feed "groups" card, sitemap | hidden | hidden | hidden | hidden | hidden | hidden |
| `/organization/<id>-<slug>/…`: home, members, problems, contests, courses, submissions, blogs | → login | 403 "No such organization" | 403 | 200 | 200 | 200 |
| Wrong slug | → login | 403, no redirect to the real slug | 403 | redirect | redirect | redirect |
| Request to join | → login | 403 | 403 | (already a member) | — | — |
| Invite link with a valid code | → login | **allowed**: this is how a hidden group gets members | allowed | — | — | — |
| Edit / requests / kick / invite / moderation | → login | 403 | 403 | 403 | 200 | 200 |
| `profile_select2?org_id=` (member autocomplete) | 404 | 404 | 404 | 200 | 200 | 200 |
| Private-contest error page, list of groups | not named | not named | not named | named | named | named |
| Blog post of an **open** hidden group | not public | not public | not public | readable | readable | readable |

With `is_visible = True`, every row behaves as before #117, except the first two rows for a **closed** group, below.

## Group matrix: `is_open = False`, `is_visible = True` (#134)

| Entry point | Anonymous | Outsider | Admin of another group | Member | Group admin | Superuser |
|---|---|---|---|---|---|---|
| `/organizations/`, every tab and search | hidden | hidden | hidden | listed | listed | listed |
| Home feed "groups" card, sitemap | hidden | hidden | hidden | hidden | hidden | hidden |
| `/organization/<id>-<slug>/` home, members | 200 | 200 | 200 | 200 | 200 | 200 |
| Problems / contests / courses / submissions | member-only gates, unchanged | | | | | |
| Request to join | → login | 200 | 200 | (already a member) | — | — |
| Private-contest error page, list of groups | named | named | named | named | named | named |

The contest error page uses `visible_to`, not `listed_to`: it only reaches someone who already holds the contest's link, and naming the group is how they learn whom to ask.

## Course matrix

A course is "visible" to a viewer in either of two cases:

- the viewer is enrolled, or is a superuser;
- the course is `is_public`, and it has no group or belongs to one of the viewer's groups.

| Entry point | Anonymous | Outsider | Group member, not enrolled | Student | Teacher / Assistant | TE of another course | Superuser |
|---|---|---|---|---|---|---|---|
| `/courses/?tab=all` and search | public, no group | public, no group | + public courses of the group | + enrolled courses | + enrolled courses | own courses | all |
| `/course/<slug>` of a **visible** course | enrolment page | enrolment page | enrolment page | course | course | enrolment page | course |
| `/course/<slug>` of a course **not visible** to the viewer | → login | 404 "No such course" | 404 | — | — | 404 | — |
| Lessons, grades, submissions | → login | 403 | 403 | 200 | 200 | 403 | 200 |
| Edit, lessons editor, members | → login | 404 | 404 | 404 | 200 | 404 | 200 |
| Home feed courses card, anonymous | public courses with no group only | | | | | | |
| `course_lesson_select2` (admin quiz picker) | none | lessons of courses the viewer can edit | | | | | all |

## Community admission and manager access

`is_community` enables community moderation and moderator roles. Admission uses the same
settings as other groups. Managers can choose direct entry, approval or private access.
Saving a community preserves that choice. Existing communities retain their stored settings.

Group managers can access member-only content in groups they manage, including while a group
is suspended. This access does not require membership and lets managers administer the group.
Members and outsiders remain subject to the suspension and membership gates.

Public courses with direct entry turned off accept applications for review on the Members page.
Tell their teachers before deployment. A rejected applicant can submit another application.
Old duplicate group requests remain as stored history and can still appear in the pending queue.

For an always-hidden Contest scoreboard, course teachers see other students' submission results
only when they also hold the Contest's editor or scoreboard permissions. Course editor status
alone does not grant access to that Contest's protected results.

## Known residue, not covered here

- **Group tags on content.** Contest list, contest page, problem page, blog post and course cards print the names of every group the item belongs to. Today that content is itself group-restricted, so the viewer is normally a member. A public item tagged with a hidden group would still show the group's name.
- `api_v1` / `api_v2` user info return group ids, but neither view is routed in `dmoj/urls.py`.
- No "archived" state. Locking members out is a separate change.

Tests: `judge/tests/test_visibility_permissions.py`. Each row above is a test or a sub-test.
