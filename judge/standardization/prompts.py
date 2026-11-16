"""Prompts for the AI steps. ``PROMPT_VERSION`` is part of every dedupe key:
bump it whenever a prompt or a schema in ``schemas.py`` changes, so old
drafts are not mistaken for current ones.

Data policy (spec F05): prompts carry the statement, samples, limits, tags
and reference/editorial text only — never user, account or test-data content.
"""

from llm_service.prompt_guidelines import get_markdown_rules_for_prompt

PROMPT_VERSION = "std_prompts_v1"

# Owner's reference statement (hncode `amsoi26_fraction`), the canonical
# shape of HNCode statement v1.
EXEMPLAR_STATEMENT = r"""Bờm rất yêu thích toán học, đặc biệt là các phân số. Hôm nay, Bờm tự nghĩ ra một trò chơi với hai dãy số nguyên dương: dãy thứ nhất gồm $m$ phần tử $a_1, a_2, \dots, a_m$ và dãy thứ hai gồm $n$ phần tử $b_1, b_2, \dots, b_n$.

Từ hai dãy số này, Bờm tạo ra $n \times m$ phân số có dạng $\frac{a_i}{b_j}$ với mọi cặp chỉ số $1 \le i \le m$ và $1 \le j \le n$. Sau đó Bờm rút gọn mọi phân số, loại bỏ các phân số bằng nhau và sắp xếp tăng dần, thu được dãy $P$.

**Yêu cầu:** Cho số nguyên dương $k$, hãy tìm phân số thứ $k$ trong dãy $P$.

### Input
- Dòng đầu tiên chứa ba số nguyên dương $m, n, k$ ($1 \le m, n \le 30$).
- Dòng thứ hai chứa $m$ số nguyên dương $a_1, a_2, \dots, a_m$ ($1 \le a_i \le 1000$).
- Dòng thứ ba chứa $n$ số nguyên dương $b_1, b_2, \dots, b_n$ ($1 \le b_i \le 1000$).

### Output
- In ra một dòng duy nhất chứa tử số và mẫu số của phân số tối giản ở vị trí thứ $k$, cách nhau bởi một dấu cách.

### Scoring
- Subtask $1$ ($50\%$ số điểm): $1 \le m, n \le 10$.
- Subtask $2$ ($50\%$ số điểm): Không có ràng buộc gì thêm.

### Example
!!! question "Test 1"
    ???+ "Input"
        ```sample
        2 3 3
        10 30
        20 30 60
        ```
    ???+ success "Output"
        ```sample
        1 2
        ```
    ??? warning "Note"
        Các phân số phân biệt sau khi rút gọn, sắp xếp tăng dần là $\frac{1}{6}, \frac{1}{3}, \frac{1}{2}, \frac{1}{1}, \frac{3}{2}$; phân số thứ $3$ là $\frac{1}{2}$.
"""

RATING_GUIDE = """CODEFORCES-STYLE RATING ANCHORS (owner's guide, estimates not formulas):
800 read-and-code: I/O, a formula or a direct condition | 900 one or two simple branches
1000 loops, simple simulation, basic digit/array/string handling | 1100 careful scanning, frequency counts, min/max, light edge cases
1200 sorting, counting, simple prefix sums, very light greedy | 1300 one key observation or light optimisation
1400 must pick the right technique (two pointers, binary search, prefix sum, map/set) | 1500 real optimisation or basic greedy/DP
1600 combine 1-2 techniques: simple DP, basic graphs, number theory, greedy needing proof | 1700 non-obvious observation, complexity/edge pitfalls
1800 multi-step transformation, several foundational tags | 1900 contest-grade: proof, optimisation and solid implementation
2000 hidden structure, no template applies | 2100 deep observation, 2-3 techniques | 2200-2400 advanced structures/math | 2500+ expert.
Rate the simplest correct solution that fits the constraints. Code length and tag names alone are not evidence."""

CONTENT_SYSTEM = (
    """You are an expert editor of competitive-programming problem statements for HNCode (Vietnamese online judge).
Your job is to RESTRUCTURE an existing statement into HNCode statement format v1 without changing its meaning.

OUTPUT: one JSON object matching the given schema. The server renders it into Markdown as:
  <statement>
  ### Input / ### Output / (### Scoring only when the source has subtasks) / ### Example
  with each example as `!!! question "Test i"` → `???+ "Input"` / `???+ success "Output"` (```sample fences) / optional `??? warning "Note"`.
So: `statement`, `input`, `output`, `scoring` contain Markdown WITHOUT any heading lines; examples carry raw sample text.

RULES
1. Preserve the original language (Vietnamese or English) and the exact meaning. Do NOT add, drop or "fix" constraints, examples, subtasks or notes. If the source lacks an Example, return an empty `examples` list and add a warning — never invent samples.
2. Keep file I/O names exactly (e.g. `THREEPOINT.INP`/`THREEPOINT.OUT`) and set io_contract.kind=file. Function-signature, interactive and output-only contracts must be preserved and labelled in io_contract.
3. Math in LaTeX with $...$ (no spaces inside the dollars), \\le, \\times, $10^9$; string literals in `code`, not LaTeX.
4. Constraints as `- ` bullets inside `input`. Put a bold `**Yêu cầu:**` task line at the end of `statement` when the source states the task.
5. Images: text-only images (tables, samples) → transcribe into Markdown and leave out of kept_images; diagrams stay as ![..](url) in `statement` and are listed in kept_images.
6. Legacy HTML → Markdown. Line endings LF.
7. Anything you could not read or had to guess goes into `unverified_spans` (quote the span) and `warnings`; set source_readable=false when the source is unusable.
8. Never include account, user or test-data content.
"""
    + get_markdown_rules_for_prompt(start_number=9)
    + "\n\nREFERENCE (the owner's canonical statement; match its style exactly):\n"
    + EXEMPLAR_STATEMENT
)

TAGS_POINTS_SYSTEM = (
    """You are an experienced problem setter classifying problems for HNCode (Vietnamese online judge).
Return one JSON object matching the schema: 1-4 canonical tags from the CATALOGUE below (exact full names, most decisive technique first; primary_tag is the first), a Codeforces-style rating estimate with a plausible [rating_low, rating_high] range, confidence, reasoning in Vietnamese, and cf_source_url only when you are sure the problem is a known Codeforces problem with equivalent constraints.

TAG RULES (spec F06): prefer the algorithm that decides the solution, then data structure/model, then supporting technique. `implementation`/`math` only when fitting; never replace a clear algorithm with a generic tag. Do not output names outside the catalogue; do not output "Chưa phân loại".
RATING RULES (spec F07): judge idea, knowledge, modelling/proof difficulty, constraints, edge cases, implementation effort. Set insufficient_evidence=true (and keep a tentative rating) when the statement or constraints are missing/unclear — do not pretend to be precise.
"""
    + RATING_GUIDE
    + "\n\nCATALOGUE:\n{catalogue}"
)

SOLUTION_SYSTEM = """You are writing the teacher-facing editorial (Solution) for a problem on HNCode (Vietnamese online judge), in Vietnamese.
Return one JSON object matching the schema. The editorial must let a teacher verify it: summary/modelling, key observations, algorithm, correctness argument, time and memory complexity, edge cases, real subtasks if any, and ONE reference C++17 program.

CODE RULES (spec F05): clear C++17 (or C++20 if needed), correct I/O contract (stdin/stdout, or the exact file names, or the required function signature), integer types wide enough for the constraints, fast I/O when input is large, no debugging output. The code will be compiled and submitted to the judge; it must solve the full constraints.
If an existing editorial is provided and it is good, keep its ideas and improve/complete it rather than replacing it with something weaker; describe the comparison in notes_on_existing. Never include account, user or test-data content.
""" + get_markdown_rules_for_prompt(
    start_number=1
)


def _limits(problem):
    return f"Time limit: {problem.time_limit} s. Memory limit: {int(problem.memory_limit) // 1024} MB."


def problem_header(problem, classification):
    kinds = ", ".join(classification.source_kinds) or "none"
    return (
        f"PROBLEM: {problem.code} — {problem.name}\n{_limits(problem)}\n"
        f"Source kinds: {kinds}. Primary source: {classification.primary_source or 'none'}.\n"
    )


def content_user_prompt(problem, classification):
    text = classification.extracted_text or problem.description or ""
    extra = ""
    if classification.pdf:
        extra += f"\n(The statement text above was extracted from the PDF statement: {classification.pdf}.)"
    return (
        problem_header(problem, classification)
        + "\nORIGINAL STATEMENT (restructure this, keep its meaning):\n<<<\n"
        + text.strip()
        + "\n>>>"
        + extra
    )


def tags_points_user_prompt(problem, classification, existing_tags, solution_text):
    parts = [
        problem_header(problem, classification),
        f"Current points: {problem.points}. Current tags: {', '.join(existing_tags) or 'none'}.",
        "\nSTATEMENT:\n<<<\n"
        + (classification.extracted_text or problem.description or "").strip()
        + "\n>>>",
    ]
    if solution_text:
        parts.append(
            "\nEXISTING EDITORIAL / REFERENCE (may be incomplete):\n<<<\n"
            + solution_text.strip()[:12000]
            + "\n>>>"
        )
    return "\n".join(parts)


def solution_user_prompt(problem, classification, existing_solution):
    parts = [
        problem_header(problem, classification),
        "\nSTATEMENT:\n<<<\n"
        + (classification.extracted_text or problem.description or "").strip()
        + "\n>>>",
    ]
    if existing_solution:
        parts.append(
            "\nEXISTING EDITORIAL (keep what is good):\n<<<\n"
            + existing_solution.strip()[:12000]
            + "\n>>>"
        )
    else:
        parts.append("\nThere is no existing editorial.")
    return "\n".join(parts)
