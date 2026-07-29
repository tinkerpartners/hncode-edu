# Deployment

## Repository access

This repository is **public**, which means anyone can read, clone and fork it. It does **not**
mean anyone can push — write access is granted explicitly and, today, only the three
`tinkerpartners` organization owners have it. Outside contributors fork the repo, which gives
them write access to their own fork only, and contribute by opening a pull request.

`main` is protected by the `protect-main` ruleset:

- no direct pushes — changes land through a pull request (0 approvals required, so you can
  merge your own)
- no force-pushes, so deployed history can never be rewritten under the running site
- no deletion

Organization owners are bypass actors and can push directly in a genuine emergency; GitHub
records each bypass. Note that an org owner can also edit or delete the ruleset itself — this
guards against accidents and gives an audit trail, not against a determined administrator.

**The production droplet never needs push access.** It fetches this public repo anonymously
over HTTPS, so no credential exists on the server and none of the above affects deploys.

> ⚠️ **The organization does not require two-factor authentication.** All three accounts with
> push access can change the code that runs hncode.edu.vn. Enabling the requirement is
> recommended, but it **immediately removes any member who does not already have 2FA on**, so
> give the other owners warning before switching it on.

Sanitized copies of the server configuration for the HNCode judge (droplet `lqdoj-green`,
serving `hncode.edu.vn`). These are **reference copies, not the live files** — nothing here
is read at runtime. After editing one, copy it to its real path and reload the service.

| File here | Real path on the server |
|---|---|
| `nginx/green.conf` | `/etc/nginx/sites-enabled/green` |
| `supervisor/green-{site,bridged,celery}.conf` | `/etc/supervisor/conf.d/` |
| `uwsgi/green.ini` | `/etc/uwsgi/apps-available/green.ini` |
| `local_settings.py.example` | `dmoj/local_settings.py` (gitignored) |
| `judge.yml.example` | `/root/judge/green-judge-<n>.yml` |
| `websocket-config.js.example` | `websocket/config.js` (gitignored) |

Every `CHANGEME` in `local_settings.py.example`, `judge.yml.example` and
`websocket-config.js.example` is a real secret that exists only on the server. TLS private
keys, judge auth keys, the database password, the SMTP app password and the event-daemon token
are **never** committed.

`websocket/config.js` is tracked upstream but **untracked here**: upstream ships it with the
placeholder token `lqdoj`, and the live file holds the real `EVENT_DAEMON_KEY`. It must equal
`EVENT_DAEMON_KEY` in `dmoj/local_settings.py` or every live update — submission status,
contest scoreboard, chat — silently stops arriving. See `PATCHES.md` entry 10.

## Deploy

```bash
cd /root/green-oj
git fetch origin && git merge --ff-only origin/main
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
./make_style.sh
.venv/bin/python manage.py collectstatic --noinput
supervisorctl restart green-site green-bridged green-celery
```

**Restart all three services, not just the site.** `green-bridged` and `green-celery` load
their code once at startup; restarting uwsgi alone leaves them on stale code and the failure
surfaces far from its cause. See the operational rule in `../PATCHES.md`.

**If the deploy changed anything under `resources/`, bump the CSS cache-bust token.**
`COMPRESS_ENABLED` is `False` and staticfiles uses the plain, non-hashing
`StaticFilesStorage`, so every built stylesheet is served from a stable URL
(`/static/style.css`). nginx sets no `Cache-Control` on `/static/`, so Cloudflare applies its
**4-hour** default — a deploy is live at the origin but invisible to users until that expires.
The links carry a `?v=` token for this; bump it in the same commit:

```bash
grep -rl '?v=<current-token>' templates/ | xargs sed -i 's/?v=<current-token>/?v=<new-token>/g'
```

Current token: **`20260729a`** (`style.css`, `markdown.css`, `darkmode*.css`, `course.css`).
Verify after deploying — comparing the origin against the edge, not just the origin:

```bash
curl -sI https://hncode.edu.vn/static/style.css | grep -i 'cf-cache-status\|content-length'
ssh green 'wc -c < /root/green-oj/static/style.css'   # lengths must match
```

A cleaner long-term fix would be content-hashed filenames — either enabling django-compressor
(`COMPRESS_ENABLED`/`COMPRESS_OFFLINE`, with `manage.py compress` in the deploy, as Blue does)
or switching to `ManifestStaticFilesStorage`. Both remove the token entirely; neither is done.

**One-time step when deploying the commit that untracks `websocket/config.js`.** That commit
deletes a file the server has modified in place, so the merge aborts with *"Your local changes
would be overwritten"*. Preserve the live token across it:

```bash
cp websocket/config.js /root/websocket-config.js.live   # keep the real token
git checkout -- websocket/config.js                     # tree clean, placeholder restored
git merge --ff-only origin/main                         # now removes the tracked file
cp /root/websocket-config.js.live websocket/config.js   # restore; now untracked + ignored
grep backend_auth_token websocket/config.js             # must NOT read 'lqdoj'
supervisorctl restart green-wsevent
```

After this, `git status` on the server is finally clean and later deploys need no special
handling. `green-wsevent` is the Node event daemon — it loads no Python, so ordinary deploys
do not restart it, but this one changes its config file.

Tag before deploying so rollback is a fast-forward:

```bash
git tag deploy-$(date +%Y%m%d) <previous-main-sha> && git push origin --tags
```

Rolling back code is `git merge --ff-only <tag>` plus reinstall and restart. **Migrations do
not roll back automatically** — review `manage.py migrate --plan` for destructive operations
before deploying, not after.

## Pulling new code from upstream LQDOJ

Never merge upstream in the live tree. Rehearse in a second checkout on the same droplet:

```bash
git clone /root/green-oj /root/green-oj-merge
cd /root/green-oj-merge
git remote add upstream https://github.com/LQDJudge/online-judge.git
git fetch upstream
git checkout -b sync/lqdoj-$(date +%Y%m%d) main
git merge upstream/master        # resolve conflicts using ../PATCHES.md
```

Verify against a **copy** of the database, never the live one:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run   # must report no changes
python manage.py migrate --plan                     # review before applying
./make_style.sh && python manage.py collectstatic --noinput
```

Then smoke-test the surfaces the local patches touch: `/`, `/problems/`, `/contests/`,
`/courses/`, `/organizations/`, `/about/`, a private course problem loaded as an enrolled
student, and one `/problem/<code>/pdf_description`.

Promote through a pull request — `main` is protected and refuses direct pushes:

```bash
git push origin sync/lqdoj-YYYYMMDD
gh pr create --base main --head sync/lqdoj-YYYYMMDD --title "Sync upstream LQDOJ YYYY-MM-DD"
gh pr merge --merge          # NEVER --squash or --rebase
```

**Never squash or rebase a sync PR.** The branch carries a merge commit joining our history
to `upstream/master`; flattening it destroys the recorded merge base, so the *next*
`git merge upstream/master` finds no common ancestor and degenerates into a whole-tree
conflict. Squash and rebase merging are disabled on the repo for exactly this reason, so the
buttons are not there to press.

Then run the deploy steps above on `/root/green-oj`.

### Migration numbering

We hold `0261`–`0265`; upstream's leaf is still `0260`. When upstream eventually adds its own
`0261`, the merged tree has two `0261_*` files and two leaf nodes. Fix it with:

```bash
python manage.py makemigrations --merge
```

which generates a merge migration and leaves our already-applied migrations applied. **Never
renumber a migration that is already applied in production** — it desynchronizes the tree from
the `django_migrations` table.

## Storage

Problem data lives on NFS at `/mnt/efs` (exported by the older `lqdoj` droplet) and media on
s3fs at `/mnt/cdn` (DO Spaces). Both are in `/etc/fstab`. The NFS export is mounted `rw` with
`all_squash,anonuid=1000,anongid=1000`, so test-data uploads land owned as the exporting host
creates them.

**The judge still depends on the older droplet for problem data.** Migrate storage before
decommissioning it.

## After any bulk data load

Loading submissions into the database bypasses `finished_submission()`, which is the only thing
that ever writes `judge_bestsubmission`. Until those rows are rebuilt, **every course grade and
every solved/attempted marker derived from the loaded submissions reads 0.**

Rebuild for **all** judged `(user, problem)` pairs, not just the ones that look relevant today —
a scope-limited backfill leaves a hole that surfaces weeks later, when a lesson is created or a
student is enrolled and the old submissions are never re-judged. Verify with the `LEFT JOIN …
IS NULL` query in `../PATCHES.md` ("Not patches"); it must return 0, and note that a plain
`JOIN` cannot detect this class of gap at all.

The same applies to a bulk change of `judge_problem.points` made outside the ORM: no admin log,
no reversion record, no rescore, and no `BestSubmission` rebuild. Re-point through the admin or
a `manage.py` command and follow it with a rejudge.
