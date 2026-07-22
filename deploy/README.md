# Deployment

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

Every `CHANGEME` in `local_settings.py.example` and `judge.yml.example` is a real secret that
exists only on the server. TLS private keys, judge auth keys, the database password and the
SMTP app password are **never** committed.

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

Promote by fast-forward only, so the live tree is never a conflict-resolution site:

```bash
git checkout main && git merge --ff-only sync/lqdoj-YYYYMMDD && git push origin main
```

then run the deploy steps above on `/root/green-oj`.

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
