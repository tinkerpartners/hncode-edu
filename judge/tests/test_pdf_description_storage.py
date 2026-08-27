"""
Regression tests for where a problem's PDF statement is written.

`Problem.pdf_description` declares `storage=problem_data_storage` (migration 0262
moved it back there from MEDIA_ROOT), and `ProblemPdfDescriptionView` reads it
through that same storage. The direct-upload pipeline, however, wrote every file
with `default_storage`. The upload reported success, the admin form showed a
filename, and `/problem/<code>/pdf_description` 404'd forever because the bytes
were in MEDIA_ROOT and the view was looking in the problem data tree.

These tests pin the invariant that actually matters: **a file is written to the
same storage the field reads it back from.**
"""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase

from judge.models import Language, Problem, ProblemGroup, ProblemType, Profile
from judge.models.problem import problem_pdf_upload_dir
from judge.models.problem_data import problem_data_storage
from judge.utils.upload_handler import UploadHandler, get_field_storage
from judge.widgets.direct_upload import DirectUploadPDFWidget

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


class TempProblemDataRootMixin:
    """
    Point problem_data_storage at a temp dir for the duration of a test.

    The storage is a module-level singleton that captured
    settings.DMOJ_PROBLEM_DATA_ROOT at import time, so override_settings does not
    reach it -- its `location`/`base_location` cached_properties must be replaced.
    """

    def use_temp_problem_root(self):
        tmp = tempfile.mkdtemp(prefix="pdftest-")
        saved = {
            k: problem_data_storage.__dict__.get(k, KeyError)
            for k in ("location", "base_location")
        }

        def restore():
            for k, v in saved.items():
                if v is KeyError:
                    problem_data_storage.__dict__.pop(k, None)
                else:
                    problem_data_storage.__dict__[k] = v
            shutil.rmtree(tmp, ignore_errors=True)

        problem_data_storage.__dict__["base_location"] = tmp
        problem_data_storage.__dict__["location"] = tmp
        self.addCleanup(restore)
        return tmp


class FieldStorageResolutionTest(TestCase):
    """get_field_storage() must follow the field, not assume default_storage."""

    def test_pdf_description_does_not_use_default_storage(self):
        field = Problem._meta.get_field("pdf_description")
        self.assertIs(field.storage, problem_data_storage)
        self.assertIsNot(
            field.storage,
            default_storage,
            "pdf_description moved back to default_storage -- the direct-upload "
            "pipeline and ProblemPdfDescriptionView must move with it.",
        )

    def test_resolves_declared_storage(self):
        self.assertIs(
            get_field_storage("judge.Problem", "pdf_description"),
            problem_data_storage,
        )

    def test_falls_back_to_default_storage(self):
        # Fields without an explicit storage keep working exactly as before.
        self.assertIs(
            get_field_storage("judge.Profile", "profile_image"), default_storage
        )
        for bad in (
            ("", ""),
            ("nope.Nope", "x"),
            ("malformed", "x"),
            ("judge.Problem", "not_a_field"),
            (None, None),
        ):
            self.assertIs(get_field_storage(*bad), default_storage, bad)


class UploadTokenTest(TestCase):
    """The signed local-upload token must carry the target field."""

    def test_roundtrip_carries_model_and_field(self):
        class FakeProfile:
            id = 7

        config = UploadHandler.get_upload_config(
            profile=FakeProfile(),
            upload_to="26tq_debai_d1",
            filename="statement.pdf",
            content_type="application/pdf",
            file_size=1024,
            max_size=10 * 1024 * 1024,
            prefix="problem",
            object_id=2829,
            model_name="judge.Problem",
            field_name="pdf_description",
        )
        self.assertEqual(config["storage_type"], "local")
        data = UploadHandler.verify_token(config["token"], 7)
        self.assertIsNotNone(data)
        self.assertEqual(data["model_name"], "judge.Problem")
        self.assertEqual(data["field_name"], "pdf_description")
        self.assertIs(
            get_field_storage(data["model_name"], data["field_name"]),
            problem_data_storage,
        )

    def test_legacy_five_field_token_still_verifies(self):
        # Tokens minted before this fix have a 1h TTL; they must not break
        # mid-upload across a deploy, just fall back to default_storage.
        import time

        payload = f"7:profile_images/x.png:image/png:0:{int(time.time()) + 600}"
        token = f"{payload}:{UploadHandler._sign_token(payload)}"
        data = UploadHandler.verify_token(token, 7)
        self.assertIsNotNone(data)
        self.assertEqual(data["model_name"], "")
        self.assertIs(
            get_field_storage(data["model_name"], data["field_name"]),
            default_storage,
        )


class ProblemPdfPathTest(TestCase):
    """New uploads land in the problem's own directory."""

    def test_upload_dir_is_the_problem_code(self):
        self.assertEqual(
            problem_pdf_upload_dir(Problem(code="26tq_debai_d1")), "26tq_debai_d1"
        )

    def test_widget_resolves_callable_upload_to_against_instance(self):
        widget = DirectUploadPDFWidget(
            upload_to=problem_pdf_upload_dir, prefix="problem"
        )
        self.assertEqual(widget.get_upload_to(), "")  # no instance yet
        widget.set_object_info(1, "judge.Problem", 2829, Problem(code="26tq_debai_d1"))
        self.assertEqual(widget.get_upload_to(), "26tq_debai_d1")


class PdfDescriptionEndToEndTest(TempProblemDataRootMixin, TestCase):
    """Upload a PDF the way the widget does, then serve it back."""

    def setUp(self):
        cache.clear()
        self.root = self.use_temp_problem_root()
        self.language, _ = Language.objects.get_or_create(
            key="PY3PDF",
            defaults={
                "name": "Python 3",
                "short_name": "PY3",
                "common_name": "Python",
                "ace": "python",
                "pygments": "python3",
                "template": "",
            },
        )
        self.user = User.objects.create_user("pdfadmin", password="pw")
        self.user.is_superuser = self.user.is_staff = True
        self.user.save()
        self.profile, _ = Profile.objects.get_or_create(
            user=self.user, defaults={"language": self.language}
        )
        group, _ = ProblemGroup.objects.get_or_create(
            name="pdfgroup", defaults={"full_name": "PDF Group"}
        )
        ptype, _ = ProblemType.objects.get_or_create(
            name="pdftype", defaults={"full_name": "PDF Type"}
        )
        self.problem = Problem.objects.create(
            code="pdfprob",
            name="PDF Problem",
            description="",
            time_limit=1.0,
            memory_limit=65536,
            points=1,
            group=group,
            partial=False,
            is_public=True,
        )
        self.problem.types.add(ptype)
        self.problem.authors.add(self.profile)

    def tearDown(self):
        cache.clear()

    def _attach_pdf(self):
        """Write + attach exactly as the fixed pipeline does."""
        storage = get_field_storage("judge.Problem", "pdf_description")
        key = f"{problem_pdf_upload_dir(self.problem)}/statement.pdf"
        saved = storage.save(key, ContentFile(MINIMAL_PDF))
        self.problem.pdf_description = saved
        self.problem.save(update_fields=["pdf_description"])
        return saved

    def test_file_lands_in_problem_data_storage_not_media(self):
        saved = self._attach_pdf()
        self.assertEqual(saved, "pdfprob/statement.pdf")
        self.assertTrue(os.path.exists(os.path.join(self.root, saved)))
        self.assertTrue(problem_data_storage.exists(saved))

    def test_field_resolves_through_its_own_storage(self):
        saved = self._attach_pdf()
        self.problem.refresh_from_db()
        field_storage = self.problem.pdf_description.storage
        self.assertIs(field_storage, problem_data_storage)
        self.assertTrue(
            field_storage.exists(self.problem.pdf_description.name),
            "the field names a path its own storage cannot find -- this is "
            "exactly the 404 bug",
        )
        self.assertEqual(saved, self.problem.pdf_description.name)

    def test_view_serves_the_pdf(self):
        self._attach_pdf()
        self.client.force_login(self.user)
        response = self.client.get("/problem/pdfprob/pdf_description")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertEqual(response.content, MINIMAL_PDF)

    def test_pdf_follows_a_code_rename(self):
        self._attach_pdf()
        self.problem.code = "pdfprob2"
        self.problem.save()
        self.problem.refresh_from_db()

        self.assertTrue(
            self.problem.pdf_description.storage.exists(
                self.problem.pdf_description.name
            ),
            "renaming the problem code stranded the PDF",
        )
        self.client.force_login(self.user)
        response = self.client.get("/problem/pdfprob2/pdf_description")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))
