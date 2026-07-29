"""The navigation-bar cache.

_nav_bar() feeds every page's navigation, so a bad cached value blanks the nav
site-wide. It used to cache an empty result with timeout=None — Django's "cache
forever" — which meant that if anything read the nav while judge_navigationbar
was empty, the blank nav was pinned until someone called .dirty() by hand. The
post_save/post_delete signal did not save you, because the rows that ended the
empty window typically arrived without signals: a mysql-level restore, loaddata
or bulk_create.
"""

from django.core.cache import cache
from django.db.models.signals import post_save
from django.test import TestCase

from judge.models import NavigationBar
from judge.signals.interface import navbar_update
from judge.template_context import _nav_bar


class NavBarCacheTest(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        NavigationBar.objects.all().delete()

    def _make(self, key, order):
        return NavigationBar.objects.create(
            key=key, label=key, path=f"/{key}/", regex=f"^/{key}/", order=order
        )

    def _make_without_signal(self, key, order):
        """Create a row the way a SQL restore does — no post_save receiver."""
        post_save.disconnect(navbar_update, sender=NavigationBar)
        try:
            return self._make(key, order)
        finally:
            post_save.connect(navbar_update, sender=NavigationBar)

    def test_rows_arriving_without_signals_are_picked_up(self):
        # list() so this asserts the caching behaviour, not the return type.
        # The window where the table is empty — e.g. mid-restore.
        self.assertEqual(list(_nav_bar()), [])

        # Rows land without firing post_save, so nothing dirties the cache.
        self._make_without_signal("home", 1)

        # The empty result must not have been cached, or the nav stays blank.
        self.assertEqual([n.key for n in _nav_bar()], ["home"])

    def test_empty_result_is_not_cached(self):
        self.assertEqual(list(_nav_bar()), [])
        self.assertIsNone(cache.get("nb:"))

    def test_populated_result_is_cached_and_reused(self):
        self._make("home", 1)
        self.assertEqual([n.key for n in _nav_bar()], ["home"])
        with self.assertNumQueries(0):
            self.assertEqual([n.key for n in _nav_bar()], ["home"])

    def test_cached_value_is_a_plain_list(self):
        self._make("home", 1)
        _nav_bar()
        self.assertIsInstance(cache.get("nb:"), list)

    def test_saving_a_nav_item_still_invalidates(self):
        self._make("home", 1)
        self.assertEqual(len(_nav_bar()), 1)
        self._make("problems", 2)
        self.assertEqual([n.key for n in _nav_bar()], ["home", "problems"])

    def test_deleting_a_nav_item_still_invalidates(self):
        self._make("home", 1)
        item = self._make("problems", 2)
        self.assertEqual(len(_nav_bar()), 2)
        item.delete()
        self.assertEqual([n.key for n in _nav_bar()], ["home"])
