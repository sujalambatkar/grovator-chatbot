"""Fixed (0-token) answers only when confident; several questions are answered one by one; greetings, the open page and the yes/no rule stay in their place."""
import pytest

from app.chat.splitter import split_questions, strip_greeting
from app.guardrails.format import drop_stray_verdict
from app.llm.prompts import GENERAL_SYSTEM, PLATFORM_NOTE, YES_NO_HINT
from tests.conftest import FakeLLM
from tests.test_appkb import GENERIC, build, turn
from tests.test_generic_kb import retriever  # noqa: F401  (fixture)


# ---- splitter ---------------------------------------------------------------------------------------------------------------
def test_split_questions():
    assert split_questions("What is I2C?") == (["What is I2C?"], 0)
    assert split_questions("Do I need the Pause block? Is the Start block required?")[0] == ["Do I need the Pause block?", "Is the Start block required?"]
    assert split_questions("Q one here?\nQ two here?\nQ three here?\nQ four here?\nQ five here?") == (["Q one here?", "Q two here?", "Q three here?"], 2)
    assert split_questions("how do I connect it?\nthanks")[0] == ["how do I connect it?"]   # a thank-you is not a question


def test_greetings_are_dropped_but_a_lone_greeting_is_kept():
    assert strip_greeting("hello tell me about grovator") == "tell me about grovator"
    assert strip_greeting("Hi there, what is I2C?") == "what is I2C?"
    assert strip_greeting("hello") == "hello"


def test_stray_yes_is_removed_only_when_the_question_is_not_yes_no():
    assert drop_stray_verdict("what is pressure?", "Yes. Pressure is a force.") == "Pressure is a force."
    assert drop_stray_verdict("is pressure a force?", "Yes. Pressure is a force.") == "Yes. Pressure is a force."


# ---- through the whole service ------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_several_questions_are_each_answered(retriever):  # noqa: F811
    q = "Do I need the Calculate Distance block?\nIs the Start block required?\nDo I need the Get Weight block?"
    d = await turn(build(retriever, FakeLLM("x"), **GENERIC), q, "multi-1", page="devices/alcohol_sensor")
    a = d["answer"]
    assert a.index("### 1.") < a.index("### 2.") < a.index("### 3.")
    assert "Calculate Distance" in a.split("### 2.")[0] and "Start" in a.split("### 3.")[0].split("### 2.")[1] and "Get Weight" in a.split("### 3.")[1]
    assert not d["warnings"] or all(w["id"] != "water" for w in d["warnings"])


@pytest.mark.asyncio
async def test_more_than_three_questions_are_capped_with_a_note(retriever):  # noqa: F811
    q = "\n".join(f"Is there a block for the buzzer number {i}?" for i in range(5))
    d = await turn(build(retriever, FakeLLM("x"), **GENERIC), q, "multi-2")
    assert d["answer"].count("### ") == 3 and "Please send the others again" in d["answer"]


@pytest.mark.asyncio
async def test_tell_me_about_grovator_is_the_platform_not_the_open_page(retriever):  # noqa: F811
    llm = FakeLLM("Grovator is a learning platform.")
    d = await turn(build(retriever, llm, **GENERIC), "hello tell me about grovator", "plat-1", page="devices/alcohol_sensor")
    assert llm.calls[0][0]["content"] == GENERAL_SYSTEM and PLATFORM_NOTE in llm.calls[0][-1]["content"]
    assert "Alcohol" not in d["answer"] and not d["warnings"] and d["intent"] == "general_tech"


@pytest.mark.asyncio
async def test_a_long_rambling_message_goes_to_the_model_not_a_template(retriever):  # noqa: F811
    q = ("so I was trying out my project yesterday with my friend and we found that nothing worked properly at all and honestly I am confused about "
         "everything that is going on with the kit and the wires and how do I connect the ultrasonic sensor properly to it")
    llm = FakeLLM("model answer")
    d = await turn(build(retriever, llm, **GENERIC), q, "long-1")
    assert d.get("canned") != "sensor_connect" and llm.calls


@pytest.mark.asyncio
async def test_a_what_is_answer_never_starts_with_yes(retriever):  # noqa: F811
    llm = FakeLLM("Yes. Pressure is a force on a surface.")
    d = await turn(build(retriever, llm, **GENERIC), "what is pressure", "stray-1")
    assert not d["answer"].lower().startswith("yes") and "Pressure is a force" in d["answer"]
    assert llm.calls[0][0]["content"] == GENERAL_SYSTEM   # no yes/no rule for a "what is" question


@pytest.mark.asyncio
async def test_the_yes_no_rule_is_added_only_for_real_yes_no_questions(retriever):  # noqa: F811
    llm = FakeLLM("Yes. GPIO32 can be digital or analog, but not both at once.")
    await turn(build(retriever, llm, **GENERIC), "Is GPIO32 able to be digital and analog at the same time?", "yn-hint-1")
    assert llm.calls and llm.calls[0][0]["content"].endswith(YES_NO_HINT)


class Flaky(FakeLLM):
    async def answer_stream(self, messages):
        self.reply = "I'm sorry, but I can't help with that." if not self.calls else "Alcohol is a kind of chemical liquid."
        async for x in super().answer_stream(messages):
            yield x


@pytest.mark.asyncio
async def test_a_refusal_of_a_harmless_science_question_is_retried_once(retriever):  # noqa: F811
    llm = Flaky()
    d = await turn(build(retriever, llm, **GENERIC), "What is alcohol?", "refuse-1")
    assert len(llm.calls) == 2 and d["answer"].startswith("Alcohol is") and "sorry" not in d["answer"].lower() and not d["warnings"]


@pytest.mark.asyncio
async def test_one_failed_question_does_not_hide_the_others(retriever):  # noqa: F811
    from app.llm.llm_client import LLMError

    class Boom(FakeLLM):
        async def answer_stream(self, messages):
            raise LLMError("down")
            yield  # pragma: no cover

    q = "Is there a block for the buzzer?\nWhat is pressure?"
    d = await turn(build(retriever, Boom("x"), **GENERIC), q, "multi-fail")
    assert "### 1." in d["answer"] and "Buzzer" in d["answer"].split("### 2.")[0] and "could not answer this one" in d["answer"].split("### 2.")[1]
