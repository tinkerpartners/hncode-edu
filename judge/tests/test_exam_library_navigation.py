from importlib import import_module
from types import SimpleNamespace

from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.urls import reverse

from judge.models import NavigationBar


class ExamLibraryNavigationTests(TestCase):
    def test_navigation_migration_restores_custom_labels_and_preserves_new_items(self):
        apps = (
            MigrationExecutor(connection)
            .loader.project_state([("judge", "0294_exam_library_navigation")])
            .apps
        )
        menu = apps.get_model("judge", "NavigationBar").objects
        menu.all().delete()
        first = menu.create(
            key="problems",
            label="Exercises",
            path="/problems/",
            regex="^/problem",
            parent_id=None,
            order=1,
            tree_id=1,
            level=0,
            lft=1,
            rght=2,
        )
        library = menu.create(
            key="library",
            label="Tài liệu riêng",
            path="/library/",
            regex="^/library",
            parent_id=None,
            order=2,
            tree_id=2,
            level=0,
            lft=1,
            rght=2,
        )
        about = menu.create(
            key="about",
            label="About us",
            path="/about/",
            regex="^/about/",
            parent_id=None,
            order=3,
            tree_id=3,
            level=0,
            lft=1,
            rght=2,
        )
        original = {
            entry.pk: (
                entry.label,
                entry.path,
                entry.regex,
                entry.parent_id,
                entry.order,
            )
            for entry in (first, library, about)
        }
        migration = import_module("judge.migrations.0294_exam_library_navigation")
        schema = SimpleNamespace(connection=connection)
        cache.set("nb:", "stale-menu")
        with self.captureOnCommitCallbacks(execute=True):
            migration.add_library_navigation(apps, schema)
        self.assertIsNone(cache.get("nb:"))
        exams = menu.get(key="exams")
        self.assertEqual(
            list(
                menu.filter(parent_id=None)
                .order_by("tree_id")
                .values_list("key", flat=True)
            ),
            ["problems", "exams", "about"],
        )
        library.refresh_from_db()
        self.assertEqual(library.parent_id, exams.pk)
        added = menu.create(
            key="custom",
            label="New page",
            path="/new/",
            regex="^/new/",
            parent_id=exams.pk,
            order=10,
            tree_id=exams.tree_id,
            level=1,
            lft=4,
            rght=5,
        )
        with self.captureOnCommitCallbacks(execute=True):
            migration.restore_navigation(apps, schema)
        for pk, values in original.items():
            entry = menu.get(pk=pk)
            self.assertEqual(
                (entry.label, entry.path, entry.regex, entry.parent_id, entry.order),
                values,
            )
        added.refresh_from_db()
        self.assertIsNone(added.parent_id)
        self.assertFalse(menu.filter(key="exams").exists())

    def test_library_nav_labels_follow_interface_language(self):
        NavigationBar.objects.all().delete()
        exams = NavigationBar.objects.create(
            key="exams", label="Thư viện đề thi", path="/library/exams/", order=1
        )
        NavigationBar.objects.create(
            key="library", label="Kho tài liệu", path="/library/", parent=exams, order=1
        )
        self.addCleanup(cache.delete, "nb:")
        cache.delete("nb:")

        response = self.client.get(
            reverse("exam_library"), headers={"accept-language": "en"}
        )
        self.assertContains(response, 'aria-label="Menu: Exam Library"')
        self.assertContains(response, "Document Library")

        response = self.client.get(
            reverse("exam_library"), headers={"accept-language": "vi"}
        )
        self.assertContains(response, 'data-tooltip="Thư viện đề thi"')
        self.assertContains(response, "Kho tài liệu")
