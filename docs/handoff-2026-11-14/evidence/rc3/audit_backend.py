import cProfile
import django
import gzip
import json
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client

from judge.models import Contest, Course, CourseRole, Problem, Profile, Submission

ROOT = Path('/code/.local/qa/rc3/performance')
ROOT.mkdir(parents=True, exist_ok=True)
ROUTES = [
    ('home', '/'),
    ('courses', '/courses/'),
    ('course', '/course/qa-ranking-deep'),
    ('lesson', '/course/qa-ranking-card-1/lesson/8'),
    ('course_grades', '/course/qa-ranking-deep/grades'),
    ('lesson_grades', '/course/qa-ranking-deep/grades/lesson/6'),
    ('contest_ranking', '/contest/qa_rank_live/ranking/'),
    ('contest_ranking_ajax', '/contest/qa_rank_live/ranking/?ajax=1'),
    ('problem', '/course/qa-ranking-card-1/lesson/8/problem/qa_sum'),
    ('ide', '/course/qa-ranking-card-1/lesson/8/problem/qa_sum/ide'),
]


class SQLProbe:
    def __init__(self):
        self.queries = []

    def __call__(self, execute, sql, params, many, context):
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.queries.append((sql, (time.perf_counter()-started)*1000))


def run(client, route):
    probe = SQLProbe()
    started = time.perf_counter()
    with connection.execute_wrapper(probe):
        response = client.get(route)
        body = response.content
    return response, {'ms': (time.perf_counter()-started)*1000, 'sql_count': len(probe.queries), 'sql_ms': sum(q[1] for q in probe.queries)}, probe


def summarize(probe):
    groups = defaultdict(list)
    for sql, elapsed in probe.queries:
        key = re.sub(r'\b\d+\b', '?', sql)
        groups[key].append(elapsed)
    return sorted([{'count': len(times), 'total_ms': round(sum(times), 3), 'sql': sql[:600]} for sql, times in groups.items()], key=lambda x: (-x['count'], -x['total_ms']))[:10]


results = []
assets = {}
for username in ('qa_rank_001', 'admin'):
    client = Client()
    client.force_login(User.objects.get(username=username))
    routes = list(ROUTES)
    for label, route in ROUTES:
        if label in ('course_grades', 'lesson_grades'):
            html = client.get(route).content.decode()
            match = re.search(r'data-grades-version="([^"]+)"', html)
            if match:
                routes.append((label+'_poll_unchanged', route+'?live=1&since='+match.group(1)))
                routes.append((label+'_poll_changed', route+'?live=1&since=outdated'))
    for label, route in routes:
        warm, warm_metrics, _ = run(client, route)
        samples = []
        last = None
        for _ in range(5):
            response, metrics, probe = run(client, route)
            samples.append(metrics)
            last = probe
        html = response.content
        row = {
            'user': username, 'label': label, 'url': route, 'status': response.status_code,
            'warmup_ms': round(warm_metrics['ms'], 2),
            'median_ms': round(statistics.median(s['ms'] for s in samples), 2),
            'min_ms': round(min(s['ms'] for s in samples), 2),
            'max_ms': round(max(s['ms'] for s in samples), 2),
            'sql_median': statistics.median(s['sql_count'] for s in samples),
            'sql_ms_median': round(statistics.median(s['sql_ms'] for s in samples), 2),
            'html_bytes': len(html), 'gzip_estimate_bytes': len(gzip.compress(html)) if html else 0,
            'top_query_groups_last_sample': summarize(last), 'samples': samples,
        }
        results.append(row)
        print(json.dumps({k:row[k] for k in ('user','label','status','median_ms','sql_median','html_bytes')}, ensure_ascii=False), flush=True)
        if label in ('home', 'problem', 'ide', 'course_grades') and username=='qa_rank_001':
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            assets[label] = {
                'url': route,
                'scripts': [s.get('src') for s in soup.select('script[src]')],
                'styles': [s.get('href') for s in soup.select('link[rel=stylesheet]')],
                'inline_script_bytes': sum(len(s.get_text().encode()) for s in soup.select('script:not([src])')),
                'dom_elements': len(soup.find_all()),
            }
    if username=='qa_rank_001':
        import pstats
        profile = cProfile.Profile()
        profile.enable()
        client.get('/course/qa-ranking-deep/grades')
        profile.disable()
        with (ROOT/'course-grades-profile.txt').open('w') as f:
            pstats.Stats(profile, stream=f).strip_dirs().sort_stats('cumulative').print_stats(45)

metadata = {
    'django': django.get_version(), 'debug': settings.DEBUG,
    'compress_enabled': settings.COMPRESS_ENABLED,
    'data': {'profiles': Profile.objects.count(), 'courses': Course.objects.count(), 'course_roles': CourseRole.objects.count(), 'problems': Problem.objects.count(), 'submissions': Submission.objects.count(), 'contests': Contest.objects.count()},
    'method': 'Django TestClient, real local DB/cache, warmup + five serial samples per route/user, includes middleware/rendering; excludes network/browser; no cache.clear; no production load test',
}
(ROOT/'backend-results.json').write_text(json.dumps({'metadata':metadata,'results':results}, ensure_ascii=False, indent=2), encoding='utf-8')
(ROOT/'page-assets.json').write_text(json.dumps(assets,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(metadata,ensure_ascii=False), flush=True)
