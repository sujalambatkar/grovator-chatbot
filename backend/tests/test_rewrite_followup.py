"""Follow-ups that rework the previous answer ("simplify it", "give me an example"): never a fresh search, never the open page."""
import pytest

from app.llm.prompts import REWRITE_SYSTEM
from tests.conftest import FakeLLM
from tests.test_appkb import GENERIC, build, turn
from tests.test_block_palette import KB
from tests.test_generic_kb import retriever  # noqa: F401  (fixture)


@pytest.mark.parametrize("q", ["simplify it", "explain it in simple words", "can you explain that again", "give me an example", "make it shorter",
                               "I didn't understand", "more detail please", "what do you mean"])
def test_rewrite_requests_are_recognised(q):
    assert KB.is_rewrite_request(q, KB.detect(q)), q


@pytest.mark.parametrize("q", ["simplify the alcohol sensor", "how do I connect it", "what is a relay", "Which blocks do I need?",
                               "explain in simple words how a relay works and how to wire it to my board and what blocks I need please"])
def test_new_questions_are_not_rewrite_requests(q):
    assert not KB.is_rewrite_request(q, KB.detect(q)), q


@pytest.mark.asyncio
async def test_simplify_it_reworks_the_previous_answer_not_the_open_page(retriever):  # noqa: F811
    """User report: after an ultrasonic explanation, "simplify it" on the Alcohol Sensor page returned the alcohol sensor's wiring guide."""
    llm = FakeLLM("distance = speed x time / 2, with a worked example.")
    svc = build(retriever, llm, **GENERIC)
    await turn(svc, "how ultrasonic sensors calculate distance", "rw-1", page="devices/alcohol_sensor")
    llm.calls.clear()
    llm.reply = "Think of an echo: the sensor times how long the sound takes to come back, then halves it."
    d = await turn(svc, "simplify it", "rw-1", page="devices/alcohol_sensor")
    assert llm.calls and llm.calls[0][0]["content"] == REWRITE_SYSTEM
    assert "PREVIOUS ANSWER" in llm.calls[0][-1]["content"] and "distance = speed" in llm.calls[0][-1]["content"]
    assert "Alcohol" not in d["answer"] and not d["warnings"] and d.get("canned") is None
    assert "echo" in d["answer"]


@pytest.mark.asyncio
async def test_a_follow_up_without_a_previous_answer_asks_what_to_explain(retriever):  # noqa: F811
    llm = FakeLLM("x")
    d = await turn(build(retriever, llm, **GENERIC), "simplify it", "rw-2", page="devices/alcohol_sensor")
    assert not llm.calls and "Ask a question first" in d["answer"] and "Alcohol" not in d["answer"]


@pytest.mark.asyncio
async def test_a_rewritten_explanation_stays_free_of_wiring(retriever):  # noqa: F811
    llm = FakeLLM("distance = speed x time / 2.")
    svc = build(retriever, llm, **GENERIC)
    await turn(svc, "how ultrasonic sensors calculate distance", "rw-3")
    llm.reply = "It times an echo.\n- Connect VCC to 5V and TRIG to GPIO5.\nHalf the trip time gives the distance."
    d = await turn(svc, "explain it simpler", "rw-3")
    assert "GPIO" not in d["answer"] and "VCC" not in d["answer"] and "echo" in d["answer"]


def test_follow_up_and_explain_prompts_are_short_and_aimed_at_6th_grade():
    from app.llm.prompts import EXPLAIN_SYSTEM
    from app.rag.tokens import count_tokens
    assert count_tokens(REWRITE_SYSTEM) <= 200 and count_tokens(EXPLAIN_SYSTEM) <= 200
    assert "6th grade" in REWRITE_SYSTEM and "11-year-old" in REWRITE_SYSTEM and "6th grade" in EXPLAIN_SYSTEM
