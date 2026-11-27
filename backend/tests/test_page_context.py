"""The open page is context only when the student points at it ("this sensor", "how do I connect it"); a general question is not about the page."""
import pytest

from tests.test_appkb import GENERIC, FakeLLM, build, turn
from tests.test_generic_kb import retriever  # noqa: F401  (fixture)

PAGE = "devices/hall_effect_sensor"


@pytest.mark.asyncio
@pytest.mark.parametrize("q", ["what are pins", "What is I2C?", "what is a sensor", "what is voltage"])
async def test_general_questions_ignore_the_open_page(retriever, q):  # noqa: F811
    llm = FakeLLM("A pin is a metal leg or hole that carries a signal.")
    svc = build(retriever, llm, **GENERIC)
    d = await turn(svc, q, "page-ctx-1", page=PAGE)
    assert "Hall Effect" not in d["answer"] and d.get("canned") != "sensor_connect", d["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [None, "devices/alcohol_sensor"])
@pytest.mark.parametrize("q", ["what is alcohol", "What is soil?", "what is pressure", "what is light", "define sound"])
async def test_a_question_about_the_substance_is_general_knowledge_not_the_sensor(retriever, q, page):  # noqa: F811
    """"What is alcohol?" must be answered about alcohol, not with the alcohol sensor's description."""
    from app.llm.prompts import GENERAL_SYSTEM
    llm = FakeLLM("Alcohol is a kind of chemical liquid.")
    svc = build(retriever, llm, **GENERIC)
    d = await turn(svc, q, "phen-" + q[:6], page=page)
    assert d["intent"] == "general_tech" and llm.calls[0][0]["content"] == GENERAL_SYSTEM and "CONTEXT" not in llm.calls[0][-1]["content"]
    assert d.get("canned") != "sensor_about" and "### What it is" not in d["answer"]


@pytest.mark.asyncio
async def test_naming_the_sensor_still_gives_the_sensor(retriever):  # noqa: F811
    d = await turn(build(retriever, FakeLLM("x"), **GENERIC), "what is the alcohol sensor", "phen-sensor")
    assert d["canned"] == "sensor_about" and "Alcohol Sensor" in d["answer"]


@pytest.mark.asyncio
async def test_it_means_the_sensor_just_discussed_not_the_open_page(retriever):  # noqa: F811
    """User report: on the Alcohol Sensor page, "how ultrasonic sensor works" then "how it calculate distance" was answered about the alcohol sensor."""
    from app.llm.prompts import EXPLAIN_SYSTEM
    llm = FakeLLM("distance = speed x time / 2")
    svc = build(retriever, llm, **GENERIC)
    await turn(svc, "how ultrasonic sensor works", "it-ctx-1", page="devices/alcohol_sensor")
    llm.calls.clear()
    d = await turn(svc, "how it calculate distance", "it-ctx-1", page="devices/alcohol_sensor")
    assert llm.calls and llm.calls[0][0]["content"] == EXPLAIN_SYSTEM
    notes = llm.calls[0][-1]["content"]
    assert "Ultrasonic Sensor" in notes and "Alcohol" not in notes, notes[:200]


@pytest.mark.asyncio
async def test_a_connection_follow_up_uses_the_conversation_sensor(retriever):  # noqa: F811
    svc = build(retriever, FakeLLM("x"), **GENERIC)
    await turn(svc, "what is the ultrasonic sensor", "it-ctx-2", page="devices/alcohol_sensor")
    d = await turn(svc, "how do I connect it", "it-ctx-2", page="devices/alcohol_sensor")
    assert "Ultrasonic Sensor" in d["answer"] and "Alcohol" not in d["answer"] and "TRIG" in d["answer"]



# ---- the open page is NOT used at all -------------------------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("q", ["how do I connect this?", "Which pins does it use?", "what are the common mistakes here?", "what is a buzzer"])
async def test_the_open_page_changes_nothing(retriever, q):  # noqa: F811
    """The bot answers from the question and the conversation only: the same message gives the same answer with or without a page open."""
    with_page = await turn(build(retriever, FakeLLM("model answer"), **GENERIC), q, "np-1", page="devices/alcohol_sensor")
    without = await turn(build(retriever, FakeLLM("model answer"), **GENERIC), q, "np-2")
    assert with_page["answer"] == without["answer"] and with_page.get("canned") == without.get("canned")


@pytest.mark.asyncio
async def test_this_with_nothing_discussed_asks_which_sensor_instead_of_guessing_from_the_page(retriever):  # noqa: F811
    d = await turn(build(retriever, FakeLLM("x"), **GENERIC), "how do I connect this", "np-3", page="devices/alcohol_sensor")
    assert "Which sensor or module do you mean" in d["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("q", ["how do I connect this", "how do I connect it?", "where do I plug it"])
async def test_a_connection_question_with_no_sensor_in_the_chat_asks_which_one(retriever, q):  # noqa: F811
    llm = FakeLLM("x")
    d = await turn(build(retriever, llm, **GENERIC), q, "ask-which-1", page="devices/alcohol_sensor")
    assert "Which sensor or module do you mean" in d["answer"] and not llm.calls
