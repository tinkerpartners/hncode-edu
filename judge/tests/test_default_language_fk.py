from django.test import TestCase, override_settings
from judge.models import Language, Profile
from django.contrib.auth.models import User


@override_settings(DEFAULT_USER_LANGUAGE="QACACHE")
class DefaultLanguageForeignKeyTests(TestCase):
    def test_replaced_language_returns_current_primary_key(self):
        old = Language.objects.create(key="QACACHE", name="QA default")
        self.assertEqual(Language.get_default_language_pk(), old.pk)
        old.delete()
        current = Language.objects.create(key="QACACHE", name="QA replacement")
        self.assertEqual(Language.get_default_language_pk(), current.pk)
        user = User.objects.create_user(username="new_default_user")
        profile = Profile.objects.create(user=user)
        self.assertEqual(profile.language_id, current.pk)

    def test_empty_database_can_create_a_valid_default(self):
        first = Language.get_default_language_pk()
        Language.objects.all().delete()
        current = Language.get_default_language_pk()
        self.assertTrue(Language.objects.filter(pk=current).exists())
        self.assertNotEqual(first, current)
