"""Public grading metadata, never test inputs/outputs, shared by prompts and checks."""

import json
import re
from dataclasses import asdict, dataclass, field

from judge.models.problem_data import ProblemData, problem_data_storage

NAMED_FILES = re.compile(r"(?i)\b([\w-]+\.(?:inp|out))\b")


def explicitly_function_only(statement):
    """Require both an explicit no-main rule and a function declaration.

    A title mentioning interaction or an editorial that happens to contain
    helper functions is not evidence of a different judge contract.
    """
    text = re.sub(r"[`*_]", "", statement or "")
    no_main = re.search(
        r"không\s+(?:được\s+)?(?:viết|cài\s*đặt|khai\s*báo)\s+(?:hàm\s+)?main\b"
        r"|do\s+not\s+(?:write|implement|include)\s+(?:a\s+)?main\b",
        text,
        re.I,
    )
    declaration = re.search(
        r"\b(?:void|int|bool|long\s+long|double)\s+(?!main\b)[A-Za-z_]\w*"
        r"\s*\([^;{}]{0,300}\)\s*;",
        text,
    )
    return bool(no_main and declaration)


@dataclass
class Contract:
    kind: str = "stdin_stdout"
    input_file: str | None = None
    output_file: str | None = None
    checker: str = "standard"
    origin: str = "default"
    issues: list = field(default_factory=list)

    def as_json(self):
        return asdict(self)

    def prompt(self):
        return (
            "GRADING CONTRACT (server metadata; not test data):\n"
            + json.dumps(self.as_json(), ensure_ascii=False)
            + "\nUse the exact case-sensitive filenames. Metadata specifies execution, "
            "not missing mathematical rules. Report contradictions instead of changing the task."
        )


def read_contract(problem, statement=""):
    contract = Contract()
    data = ProblemData.objects.filter(problem=problem).first()
    if data:
        contract.origin = "database"
        contract.input_file = data.fileio_input or None
        contract.output_file = data.fileio_output or None
        contract.checker = data.checker or "standard"
        if data.use_ioi_signature:
            contract.kind = "function"
        elif data.interactive_judge or data.is_communication:
            contract.kind = "interactive"
        elif data.output_only:
            contract.kind = "output_only"
    # init.yml is what the judge consumes. Only whitelist public metadata;
    # never pass cases, archive members, generator/checker code into a prompt.
    name = f"{problem.code}/init.yml"
    try:
        if problem_data_storage.exists(name):
            import yaml

            with problem_data_storage.open(name, "rb") as stream:
                raw = stream.read(262145)
            if len(raw) > 262144:
                contract.issues.append("grading_metadata_too_large")
            else:
                init = yaml.safe_load(raw)
                if not isinstance(init, dict):
                    contract.issues.append("invalid_grading_metadata")
                else:
                    io = init.get("file_io") or {}
                    if not isinstance(io, dict):
                        contract.issues.append("invalid_file_io")
                    else:
                        actual = (io.get("input") or None, io.get("output") or None)
                        if data and actual != (
                            contract.input_file,
                            contract.output_file,
                        ):
                            contract.issues.append("database_init_io_conflict")
                        contract.input_file, contract.output_file = actual
                    checker = init.get("checker")
                    if isinstance(checker, str):
                        contract.checker = checker
                    if init.get("signature_grader"):
                        contract.kind = "function"
                    elif init.get("interactive") or init.get("communication"):
                        contract.kind = "interactive"
                    elif init.get("output_only"):
                        contract.kind = "output_only"
                    contract.origin = "init.yml"
    except Exception:  # Missing/broken storage is not evidence of standard I/O.
        contract.issues.append("grading_metadata_unreadable")

    for attr in ("input_file", "output_file"):
        value = getattr(contract, attr)
        if value is not None and (
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", value)
            or value in (".", "..")
        ):
            contract.issues.append("unsafe_or_invalid_filename")
            setattr(contract, attr, None)
    files = set(NAMED_FILES.findall(statement or ""))
    inputs = {x for x in files if x.lower().endswith(".inp")}
    outputs = {x for x in files if x.lower().endswith(".out")}
    if contract.origin == "default" and len(inputs) == len(outputs) == 1:
        contract.input_file, contract.output_file = next(iter(inputs)), next(
            iter(outputs)
        )
        contract.origin = "statement"
    elif files and contract.origin != "default":
        if (inputs and inputs != {contract.input_file}) or (
            outputs and outputs != {contract.output_file}
        ):
            contract.issues.append("statement_judge_io_conflict")
    if contract.kind == "stdin_stdout" and (
        contract.input_file or contract.output_file
    ):
        contract.kind = "file"
    if explicitly_function_only(statement) and contract.kind in (
        "stdin_stdout",
        "file",
    ):
        if contract.origin == "default":
            contract.kind, contract.origin = "function", "statement"
        else:
            contract.issues.append("statement_judge_task_kind_conflict")
    return contract


def missing_files(contract, code):
    """A preflight, not a proof of correct I/O: the judge still runs the code."""
    return [
        name
        for name in (contract.input_file, contract.output_file)
        if name and not re.search(rf"""["'](?:\./)?{re.escape(name)}["']""", code)
    ]


def adapt_cpp_file_io(code, contract):
    """Attach file streams to a generated stdin program, without changing its algorithm.

    Refuse ambiguous cases and existing file handling. Never adapt author code.
    The resulting exact source must subsequently pass the real judge.
    """
    if contract.kind != "file" or contract.issues or not missing_files(contract, code):
        return code, False
    if 'R"' in code:  # Do not guess C++ raw-string boundaries.
        return code, False
    masked = re.sub(
        r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|(?<!\w)\'(?:\\.|[^\'\\\n])*\'',
        lambda m: "".join("\n" if c == "\n" else " " for c in m[0]),
        code,
    )
    if re.search(
        r"\b(?:freopen|fopen|ifstream|ofstream|fstream|open|system)\b", masked
    ):
        return code, False
    match = re.search(
        r"\b(?:int|signed|int32_t)\s+main\s*\(\s*(?:void\s*)?\)\s*\{", masked
    )
    if not match:
        return code, False
    calls = []
    for name, mode, stream in (
        (contract.input_file, "r", "stdin"),
        (contract.output_file, "w", "stdout"),
    ):
        if name:
            calls.append(
                f'    if (!std::freopen({json.dumps(name)}, "{mode}", {stream})) return 0;'
            )
    adapted = (
        "#include <cstdio>\n"
        + code[: match.end()]
        + "\n"
        + "\n".join(calls)
        + "\n"
        + code[match.end() :]
    )
    return adapted, True
