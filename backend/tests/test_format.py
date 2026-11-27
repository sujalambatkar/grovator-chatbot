"""Model output is made readable before it is streamed: no markdown tables, no HTML."""
import pytest

from app.guardrails.format import tidy
from app.guardrails.output import check_output

TABLE = """**FUNCTIONS BLOCKS**

| Block | What it does (plain language) | How you use it |
|------|-----------------|----------------|
| **To do something (function)** | Creates a named “mini-program”. | 1️⃣ Drag the block onto the workspace.<br>2️⃣ Give the function a name (e.g., `blinkLED`).<br>3️⃣ Put the steps inside. |
| **If … return** | Stops the function early. | Use it inside a function. |
"""


def test_table_becomes_headings_bullets_and_numbered_steps():
    out = tidy(TABLE)
    assert "|" not in out and "<br>" not in out and "---" not in out
    assert "**To do something (function)**" in out and "- **What it does (plain language):** Creates a named" in out
    assert "1. Drag the block onto the workspace." in out and "3. Put the steps inside." in out
    assert "- **How you use it:** Use it inside a function." in out


def test_html_tags_are_removed_and_br_is_a_line_break():
    assert tidy("one<br>two <b>bold</b><br/>three</p>") == "one\ntwo bold\nthree"


def test_plain_markdown_is_left_alone():
    s = "## Title\n\n1. **Step** one\n2. Step two\n\n- a\n- b"
    assert tidy(s) == s


def test_a_pipe_in_ordinary_text_is_not_a_table():
    assert tidy("Use a | b to pick one") == "Use a | b to pick one"


def test_check_output_tidies_every_model_answer():
    out = check_output(TABLE, source_text=TABLE + " To do something function Creates named mini-program Drag block workspace")
    assert "|" not in out.text and "<br>" not in out.text


@pytest.mark.asyncio
async def test_explain_those_blocks_repeats_the_blocks_with_what_they_do():
    from tests.test_block_palette import KB
    r = KB.follow_up("explain those blocks to me", "What blocks are in the Functions category?")
    assert r is not None and "To do something" in r.text and KB.follow_up("explain those blocks to me", "What is I2C?") is None


def test_latex_is_turned_into_plain_text_formulas():
    out = tidy(r"\(R_{LDR}\) and \[V_{out}=V_{CC}\;\frac{R_{LDR}}{R_{LDR}+R_{ref}}\] and 5 \times 3")
    assert "\\" not in out and "(R_LDR) / (R_LDR+R_ref)" in out and "5 × 3" in out


def test_single_asterisk_emphasis_is_shown_as_plain_text_but_bold_and_bullets_stay():
    out = tidy("*distance*: how far\n* a bullet\n**bold** stays and 2 * 3 * 4")
    assert out.startswith("distance: how far") and "* a bullet" in out and "**bold**" in out and "2 * 3 * 4" in out
