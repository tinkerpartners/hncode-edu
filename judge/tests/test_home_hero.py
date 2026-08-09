"""The admin-configurable hero section on the home page.

A singleton HomeHeroSection row drives a banner (plain text on a colored
background) plus an optional large image, rendered right after the nav bar on
the home page only. Hidden when absent or disabled; toggling the row must
invalidate the cache.
"""

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from judge.models import HomeHeroSection


class HomeHeroSectionTest(TestCase):
    def setUp(self):
        # The hero lookup is cached and the cache outlives each test's DB
        # rollback, so start every test from a cold cache.
        cache.clear()

    def _hero(self, **kwargs):
        kwargs.setdefault("enabled", True)
        kwargs.setdefault("text", "**Welcome** to the judge")
        hero = HomeHeroSection(**kwargs)
        hero.save()
        return hero

    def test_home_renders_without_a_hero_row(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "home-hero-banner")

    def test_disabled_hero_is_hidden(self):
        self._hero(enabled=False)
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "home-hero-banner")

    def test_enabled_hero_shows_banner_with_colors_and_plain_text(self):
        self._hero(background_color="#123abc", text_color="#ffffff")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "home-hero-banner")
        self.assertContains(response, "background-color: #123abc")
        self.assertContains(response, "color: #ffffff")
        # The text is rendered verbatim, not through markdown.
        self.assertContains(response, "**Welcome** to the judge")
        self.assertNotContains(response, "<strong>Welcome</strong>")

    def test_hero_image_is_rendered_below_the_banner(self):
        self._hero(image="home_hero/test.png")
        response = self.client.get(reverse("home"))
        self.assertContains(response, '<img class="home-hero-image"')
        self.assertContains(response, "home_hero/test.png")

    def test_hero_without_image_renders_banner_only(self):
        self._hero()
        response = self.client.get(reverse("home"))
        self.assertContains(response, "home-hero-banner")
        self.assertNotContains(response, '<img class="home-hero-image"')

    def test_blog_list_page_has_no_hero(self):
        self._hero()
        response = self.client.get(reverse("blog_post_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "home-hero-banner")

    def test_toggling_enabled_invalidates_the_cache(self):
        hero = self._hero()
        self.assertContains(self.client.get(reverse("home")), "home-hero-banner")
        hero.enabled = False
        hero.save()
        self.assertNotContains(self.client.get(reverse("home")), "home-hero-banner")

    def test_model_is_a_singleton(self):
        self._hero()
        self._hero(text="Second save")
        self.assertEqual(HomeHeroSection.objects.count(), 1)
        self.assertEqual(HomeHeroSection.objects.get().pk, 1)
