"""Wiring hazards: the student is told directly to stop (or not to do it), then shown the correct wiring."""
import pytest

from app.guardrails.hazard import detect_hazard
from tests.test_generic_kb import ask, retriever  # noqa: F401  (fixture)


@pytest.mark.parametrize("msg,hid,question", [
    ("i have connected ir sensors vcc to 5V ground to 5V and datapin to gpio36", "short_circuit", False),
    ("I connected GND to VCC", "short_circuit", False),
    ("can I connect ground to 5V?", "short_circuit", True),
    ("is it ok to connect gnd and 3.3v together", None, None),   # "and" is not a connection verb: left to the normal flow
    ("connect 5V to gpio36", "power_to_signal", True),
    ("I wired 5V to the data pin", "power_to_signal", False),
    ("I connected a 12V battery to the esp32", "overvoltage", False),
    ("my board is smoking", "overheating", False),
    ("the relay is on fire", "fire", False),
    ("I got an electric shock", "shock", False),
])
def test_hazards_are_detected(msg, hid, question):
    found = detect_hazard(msg)
    if hid is None:
        return
    assert found is not None and found[0].id == hid and found[1] is question, (msg, found)


@pytest.mark.parametrize("msg", ["I connected VCC to 5V, GND to GND and the signal to GPIO36", "what is a short circuit?",
                                 "how do I connect the 5V and ground of the sensor", "connect the signal wire to GPIO36", "VCC to 3.3V and GND to ground"])
def test_correct_wiring_and_concept_questions_are_not_hazards(msg):
    assert detect_hazard(msg) is None, msg


@pytest.mark.asyncio
async def test_the_student_is_told_to_stop_first_and_gets_the_correct_wiring(retriever):  # noqa: F811
    d, llm = await ask(retriever, "i have connected ir sensors vcc to 5V ground to 5V and datapin to gpio36")
    assert not llm.calls and d["canned"] == "safety_stop"
    assert d["answer"].startswith("## ⛔ Stop! Unplug the power now") and "short circuit" in d["answer"]
    assert "correct wiring for the IR Sensor" in d["answer"] and "**GND**" in d["answer"]


@pytest.mark.asyncio
async def test_a_question_about_a_dangerous_wiring_gets_a_direct_no(retriever):  # noqa: F811
    d, llm = await ask(retriever, "can I connect ground to 5V?")
    assert not llm.calls and d["answer"].startswith("## ⛔ No, do not do this")
