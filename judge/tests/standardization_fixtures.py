"""Small real manifest/files for policy/readiness tests, isolated from live data."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.storage import FileSystemStorage


def install_test_data(case, problem):
    if not hasattr(case, "_std_test_root"):
        case._std_test_root = case.enterContext(tempfile.TemporaryDirectory())
        storage = FileSystemStorage(location=case._std_test_root)
        for module in ("contracts", "hashing", "readiness"):
            case.enterContext(
                patch(f"judge.standardization.{module}.problem_data_storage", storage)
            )
    folder = Path(case._std_test_root) / problem.code
    folder.mkdir(exist_ok=True)
    (folder / "init.yml").write_text(
        "test_cases:\n- in: 1.in\n  out: 1.out\n  points: 100\n", encoding="utf-8"
    )
    (folder / "1.in").write_text("1 2\n", encoding="utf-8")
    (folder / "1.out").write_text("3\n", encoding="utf-8")
    return folder
