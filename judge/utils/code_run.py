"""The on-disk half of IDE "Run" (A22).

The judges speak only one grading protocol: "grade submission S against
problem P", where P is a directory holding an init.yml under the shared problem
root. A run reuses that protocol unchanged by writing a throwaway problem whose
test cases are the inputs the user typed, and pointing the judge at it. The judge, its sandbox and its limits do all the work; nothing executes in
the web or bridge processes, and no judge rebuild is needed.

Layout, relative to DMOJ_PROBLEM_DATA_ROOT (the judges' /problems):

    __run_<id>_<hmac>/init.yml
                      1.in, 1.out, 2.in, 2.out, ...

The directory has to sit directly in the problem root: the judge resolves a
problem id as <root>/<id> and builds temporary file names from the id, so a
nested path cannot be used. A run directory never matches a Problem row, so
when a judge rescans and reports it, the bridge ignores it.

Each judge watches the problem root and rescans its problem list on any file
event under it (debounced to at most one rescan per ~3.5 s; ~0.5 s for Green's
20k problems). Creating a run directory therefore costs each judge one rescan.
Deleting it would cost another, so finished runs are not deleted one by one:
every new run sweeps the directories older than half an hour (longer than any
queue wait), and those deletions fall into the same debounce window as the new
run's creation.
"""

import logging
import os
import re
import shutil
import time

import yaml
from django.conf import settings

logger = logging.getLogger("judge.code_run")

RUN_PREFIX = "__run_"

# A file I/O name the user may pick: a plain file name, nothing that walks
# directories or hides itself.
FILEIO_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")


def run_setting(name, default):
    return getattr(settings, "DMOJ_RUN_" + name, default)


def max_input_length():
    """Total characters of input (and of expected output) across all cases."""
    return run_setting("MAX_INPUT_LENGTH", 1 << 18)


def max_cases():
    return run_setting("MAX_CASES", 6)


def max_time_limit():
    return run_setting("MAX_TIME_LIMIT", 5.0)


def max_memory_limit():
    return run_setting("MAX_MEMORY_LIMIT", 524288)


def output_prefix_length():
    """How much of the program's output is shown back to the user."""
    return run_setting("OUTPUT_PREFIX_LENGTH", 65536)


def output_limit_length():
    """Past this much output the judge stops the program (OLE)."""
    return run_setting("OUTPUT_LIMIT_LENGTH", 1 << 20)


def is_valid_fileio_name(name):
    return bool(name) and bool(FILEIO_NAME_RE.match(name)) and ".." not in name


def _data_root():
    return settings.DMOJ_PROBLEM_DATA_ROOT


def run_dir(run):
    return os.path.join(_data_root(), run.problem_dir)


def build_init(run):
    init = {
        "test_cases": [
            {"in": "%d.in" % i, "out": "%d.out" % i, "points": 1}
            for i in range(1, len(run.cases) + 1)
        ],
        "output_prefix_length": output_prefix_length(),
        "output_limit_length": output_limit_length(),
        "checker": "standard",
    }
    file_io = {}
    if run.fileio_input:
        file_io["input"] = run.fileio_input
    if run.fileio_output:
        file_io["output"] = run.fileio_output
    if file_io:
        init["file_io"] = file_io
    return init


def _write_run_files(directory, run):
    os.makedirs(directory, mode=0o755, exist_ok=True)
    files = {"init.yml": yaml.safe_dump(build_init(run), default_flow_style=False)}
    for i, case in enumerate(run.cases, 1):
        files["%d.in" % i] = case.get("input") or ""
        files["%d.out" % i] = case.get("expected") or ""
    for name, content in files.items():
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.chmod(path, 0o644)
    os.chmod(directory, 0o755)


def prepare_run_dir(run):
    """Write the throwaway problem for ``run``; returns its absolute path."""
    target = run_dir(run)
    _write_run_files(target, run)
    return target


def remove_run_dir(run_or_dir):
    """Delete a run's directory now (a run that never reached a judge).
    Safe to call twice."""
    if isinstance(run_or_dir, str):
        directory = os.path.join(_data_root(), run_or_dir)
    else:
        directory = run_dir(run_or_dir)
    # Never let a crafted name reach anything but a run directory.
    root = os.path.realpath(_data_root())
    real = os.path.realpath(directory)
    if os.path.dirname(real) != root or not os.path.basename(real).startswith(
        RUN_PREFIX
    ):
        logger.error("Refusing to remove %s: not a run directory", directory)
        return

    shutil.rmtree(real, ignore_errors=True)


def sweep_stale_run_dirs(max_age=1800):
    """Remove run directories older than ``max_age`` seconds -- every run
    longer ago than any run can take, so only finished ones."""
    root = _data_root()
    try:
        entries = [name for name in os.listdir(root) if name.startswith(RUN_PREFIX)]
    except FileNotFoundError:
        return 0
    cutoff = time.time() - max_age
    removed = 0
    for name in entries:
        path = os.path.join(root, name)
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed
