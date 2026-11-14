"""
Markdown improver for problem descriptions
Uses LLM to propose problem statements in HNCode statement format v1.
"""

import time
from typing import Dict, Any
import logging
from llm_service.llm_api import LLMService, PoeRequestError
from llm_service.prompt_guidelines import get_markdown_rules_for_prompt

logger = logging.getLogger(__name__)


class MarkdownImprover:
    """Improves problem markdown formatting using LLM"""

    def __init__(
        self,
        api_key: str,
        bot_name: str = "Claude-Sonnet-4.6",
        sleep_time: float = 2.5,
        timeout: int = 120,
    ):
        self.llm_service = LLMService(
            api_key, bot_name, sleep_time, timeout, raise_provider_errors=True
        )
        self.sleep_time = sleep_time

    def get_format_template(self) -> str:
        """Return the target format template for problem descriptions"""
        return """Cho bốn số tự nhiên $a_1, b_1, a_2, b_2$ với $(a_1, b_1)$ là độ dài các cạnh của hình chữ nhật thứ nhất và $(a_2, b_2)$ là độ dài các cạnh của hình chữ nhật thứ hai. Hãy đưa ra diện tích hình vuông nhỏ nhất chứa được cả hai hình chữ nhật này mà các hình chữ nhật không xếp đè lên nhau hoặc thừa ra bên ngoài hình vuông.

### Input
- Dữ liệu nhập vào từ bàn phím gồm bốn dòng lần lượt là bốn số tự nhiên $a_1, b_1, a_2, b_2 (0 < a_1, b_1, a_2, b_2\\leq 10^6)$.

### Output
- In ra màn hình một số duy nhất là diện tích của hình vuông bé nhất thoả mãn yêu cầu đề bài.

### Example

!!! question "Test 1"
    ???+ "Input"
        ```sample
        2
        3
        2
        4
        ```
    ???+ success "Output"
        ```sample
        16
        ```
    ??? warning "Note"
        Ta có hai hình chữ nhật kích thước là $2\\cdot 3$ và $2\\cdot 4$. Hai hình này đặt vừa trong hình vuông nhỏ nhất kích thước $4\\cdot 4$. Vậy cần đưa ra đáp số là $16$.
        1. ![markdown](https://i.imgur.com/YsozYqp.png)

!!! question "Test 2"
    ???+ "Input"
        ```sample
        4
        5
        4
        5
        ```
    ???+ success "Output"
        ```sample
        64
        ```
    ??? warning "Note"
        Với hai hình chữ nhật kích thước là $4\\cdot 5$ và $4\\cdot 5$ thì hình vuông nhỏ nhất chứa đủ phải có kích thước $8\\cdot 8$. Vậy cần đưa ra đáp số là $64$.

"""

    def _fix_unclosed_code_blocks(self, markdown: str) -> str:
        """
        Fix unclosed code blocks in markdown.
        Counts ``` occurrences and adds closing ``` if odd.
        """
        # Count code block markers
        code_block_count = markdown.count("```")

        # If odd number, add closing ```
        if code_block_count % 2 == 1:
            markdown = markdown.rstrip() + "\n        ```"

        return markdown

    def improve_markdown(
        self,
        problem_statement: str,
        problem_name: str = None,
        problem_code: str = None,
        max_retries: int = 1,
    ) -> Dict[str, Any]:
        """
        Improve the markdown formatting of a problem statement.

        Args:
            problem_statement: The original problem statement (may include PDF reference)
            problem_name: Optional problem name for context
            problem_code: Optional problem code for context
            max_retries: Maximum number of attempts

        Returns:
            Dict with 'success', 'improved_markdown', and optional 'error' fields
        """
        template = self.get_format_template()
        markdown_rules = get_markdown_rules_for_prompt(start_number=9)

        system_prompt = f"""You are an expert at formatting competitive programming problem statements.
Your task is to propose a statement using HNCode statement format v1.

IMPORTANT FORMATTING RULES:
1. Use LaTeX math notation with $ for inline math (e.g., $n$, $a_i$, $10^9$)
2. Use level-three section headers with a space: ### Input, ### Output, ### Example.
   Include ### Scoring only when the original statement specifies subtasks.
3. Format constraints in bullet points with - prefix
4. Use the special admonition syntax for test cases:
   - !!! question "Test X" for each test case
   - ???+ "Input" with ```sample code block for input
   - ???+ success "Output" with ```sample code block for output
   - ??? warning "Note" for optional explanations (note: ??? without + means collapsed by default)
5. For images: analyze whether it is purely text (tables, sample tests, notes, constraints, explanations) or a visual diagram/illustration
   - If the image contains only text content: extract and convert it to proper markdown — do NOT keep the image link
   - If the image is a visual diagram, graph, or illustration: keep it as ![description](url)
6. Preserve the original language (Vietnamese or English)
7. Use \\times for multiplication in math mode
8. Format large numbers with scientific notation where appropriate (e.g., $10^9$)

{markdown_rules}

HERE IS THE TARGET FORMAT TEMPLATE:
{template}

CRITICAL INSTRUCTIONS:
- Analyze any PDF attachments and images carefully - they often contain the complete problem statement
- Extract ALL information: problem description, constraints, input/output format, examples, and notes
- For text-only images (sample test tables, notes, constraints): extract the text content into proper markdown, remove the image link
- Maintain the EXACT same content/meaning, just reformat to match the template style
- If there are multiple test cases, create separate !!! question blocks for each — carefully count ALL test cases regardless of layout (stacked, side-by-side columns, grids, etc.)
- Do NOT add or remove any information, only reformat
- Preserve exact case-sensitive input/output filenames in headings, text, and examples.
- Preserve function-only, output-only, and interactive contracts.
- Do not infer missing constraints, examples, or subtasks from code or this template.
- When a source cannot be read reliably, keep the source reference and mark the uncertainty.
- Output ONLY the improved markdown, no explanations or commentary"""

        problem_info = ""
        if problem_name or problem_code:
            problem_info = f"""PROBLEM INFO:
- Code: {problem_code or 'N/A'}
- Name: {problem_name or 'N/A'}

"""

        user_prompt = f"""{problem_info}Please propose the following problem statement in HNCode statement format v1.
If the content includes PDF references/attachments, analyze the PDF content and extract the problem statement from it.

ORIGINAL PROBLEM STATEMENT:
{problem_statement}

OUTPUT: Provide ONLY the reformatted markdown, nothing else."""

        for attempt in range(max_retries):
            logger.info(f"Markdown improvement attempt {attempt + 1}")

            try:
                response = self.llm_service.call_llm_with_files(
                    user_prompt, problem_statement, system_prompt
                )
            except PoeRequestError as exc:
                if (
                    exc.code not in ("timeout", "provider")
                    or attempt == max_retries - 1
                ):
                    raise
                # Retry transient failures within the caller's attempt budget.
                # Credentials/quota errors need user action, not more requests.
                response = None

            if response:
                # Clean up the response - remove any markdown code block wrappers
                improved = response.strip()

                # Remove ```markdown or ``` wrappers if present
                if improved.startswith("```markdown"):
                    improved = improved[11:]
                elif improved.startswith("```"):
                    improved = improved[3:]

                if improved.endswith("```"):
                    improved = improved[:-3]

                improved = improved.strip()

                # Fix unclosed code blocks
                improved = self._fix_unclosed_code_blocks(improved)

                if improved:
                    logger.info(f"Successfully improved markdown for problem")
                    return {
                        "success": True,
                        "improved_markdown": improved,
                    }
                else:
                    logger.warning("Empty response from LLM")
            else:
                logger.warning("Failed to get LLM response")

            # Delay before retrying
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {self.sleep_time} seconds...")
                time.sleep(self.sleep_time)

        return {
            "success": False,
            "improved_markdown": None,
            "error": "Failed to improve markdown after all attempts",
        }
