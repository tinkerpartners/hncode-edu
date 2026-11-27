"""Personal C++/Python starting code, set in Settings and used by the editor.

A problem's own template comes first. A strict contest may turn personal
templates off (the default) or allow them.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from judge.models import Language, LanguageTemplate, ProfileCodeTemplate
from judge.models.code_template import code_template_kind
from judge.tests.strict_helpers import StrictContestMixin

CPP = "#include <bits/stdc++.h>\nint main() {}\n"
PY = "import sys\ninput = sys.stdin.readline\n"


@override_settings(LANGUAGE_CODE="en")
class PersonalCodeTemplateTest(StrictContestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.cpp, _ = Language.objects.get_or_create(
            key="CPP17",
            defaults={
                "name": "C++17",
                "short_name": "C++17",
                "common_name": "C++",
                "ace": "c_cpp",
                "pygments": "cpp",
                "template": "// default C++",
            },
        )
        self.c, _ = Language.objects.get_or_create(
            key="C11",
            defaults={
                "name": "C11",
                "short_name": "C11",
                "common_name": "C",
                "ace": "c_cpp",
                "pygments": "c",
                "template": "/* default C */",
            },
        )
        self.language.template = "# default Python"
        self.language.save()

    def template(self, language, problem=None):
        params = {"id": language.id}
        if problem:
            params["problem"] = problem.id
        response = self.client.get(reverse("language_template_ajax"), params)
        self.assertEqual(response.status_code, 200)
        return response

    def save_settings(self, **templates):
        profile = self.participant
        data = {
            "first_name": "",
            "last_name": "",
            "timezone": profile.timezone,
            "language": self.language.pk,
            "ace_theme": profile.ace_theme,
            "code_theme": "",
            "tshirt_size": "",
            "date_of_birth": "",
            "address": "",
        }
        data.update(templates)
        return self.client.post(reverse("user_edit_profile"), data)

    def test_languages_map_to_template_families(self):
        self.assertEqual(code_template_kind(self.cpp), "cpp")
        self.assertEqual(code_template_kind(self.language), "python")
        # C shares Ace's c_cpp mode but must not get a C++ template.
        self.assertIsNone(code_template_kind(self.c))

    def test_settings_save_and_clear_templates(self):
        self.login()
        response = self.save_settings(template_cpp=CPP, template_python=PY)
        self.assertEqual(response.status_code, 302)
        saved = dict(self.participant.code_templates.values_list("kind", "source"))
        self.assertEqual(saved, {"cpp": CPP, "python": PY})

        page = self.client.get(reverse("user_edit_profile")).content.decode()
        self.assertIn('name="template_cpp"', page)
        self.assertIn("bits/stdc++.h", page)

        self.save_settings(template_cpp="  \n", template_python=PY)
        self.assertEqual(
            list(self.participant.code_templates.values_list("kind", flat=True)),
            ["python"],
        )

    def test_settings_form_without_template_fields_keeps_them(self):
        ProfileCodeTemplate.save_for(self.participant, "cpp", CPP)
        self.login()
        self.save_settings()
        self.assertTrue(self.participant.code_templates.filter(kind="cpp").exists())

    def test_editor_starts_from_the_personal_template(self):
        ProfileCodeTemplate.save_for(self.participant, "cpp", CPP)
        self.login()
        response = self.template(self.cpp, self.outside_problem)
        self.assertEqual(response.content.decode(), CPP)
        self.assertIn("no-cache", response["Cache-Control"])
        # Other families and other users keep the language default.
        self.assertEqual(self.template(self.c).content.decode(), "/* default C */")
        self.assertEqual(
            self.template(self.language).content.decode(), "# default Python"
        )
        self.login(self.author)
        self.assertEqual(self.template(self.cpp).content.decode(), "// default C++")

    def test_problem_template_comes_first(self):
        ProfileCodeTemplate.save_for(self.participant, "cpp", CPP)
        LanguageTemplate.objects.create(
            problem=self.outside_problem, language=self.cpp, source="// interactive"
        )
        self.login()
        self.assertEqual(
            self.template(self.cpp, self.outside_problem).content.decode(),
            "// interactive",
        )

    def test_anonymous_gets_the_language_default(self):
        self.assertEqual(self.template(self.cpp).content.decode(), "// default C++")

    def test_strict_contest_blocks_personal_templates_by_default(self):
        ProfileCodeTemplate.save_for(self.participant, "python", PY)
        self.assertFalse(self.contest.strict_allow_templates)
        self.join()
        self.login()
        self.assertEqual(
            self.template(self.language, self.problem).content.decode(),
            "# default Python",
        )

    def test_strict_contest_can_allow_personal_templates(self):
        ProfileCodeTemplate.save_for(self.participant, "python", PY)
        self.contest.strict_allow_templates = True
        self.contest.save()
        self.join()
        self.login()
        self.assertEqual(
            self.template(self.language, self.problem).content.decode(), PY
        )

    def test_non_strict_contest_uses_personal_templates(self):
        ProfileCodeTemplate.save_for(self.participant, "python", PY)
        self.contest.is_strict = False
        self.contest.save()
        self.join()
        self.login()
        self.assertEqual(
            self.template(self.language, self.problem).content.decode(), PY
        )

    def test_join_page_states_the_template_rule(self):
        self.login()
        join = reverse("contest_join", args=(self.contest.key,))
        response = self.client.post(join)
        self.assertTemplateUsed(response, "contest/strict_consent.html")
        self.assertContains(response, "Personal code template")
