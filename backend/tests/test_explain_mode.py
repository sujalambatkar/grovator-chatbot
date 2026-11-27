"""How a sensor measures: principle, formula and a worked example (general physics around the documented notes), never wiring."""
import pytest

from app.chat.appkb import Entities
from app.guardrails.format import strip_wiring
from app.llm.prompts import EXPLAIN_SYSTEM
from tests.conftest import FakeLLM
from tests.test_appkb import GENERIC, build, turn
from tests.test_block_palette import KB
from tests.test_generic_kb import retriever  # noqa: F401  (fixture)


def target(q, recent=()):
    return KB.explain_target(q, KB.detect(q), Entities(sensors=list(recent)))


def test_the_sensor_is_found_from_the_message_or_the_conversation():
    assert target("how can I read the distance in centimeters with the ultrasonic sensor") == "ultrasonic_sensor"
    assert target("now explain the same concept in detail and how can i read the dsitance value in centimeters", ["ultrasonic_sensor"]) == "ultrasonic_sensor"
    assert target("what is the formula to convert the LDR reading to lux") == "ldr_sensor"
    assert target("how is the flow rate calculated from the flow sensor") == "flow_sensor"


@pytest.mark.parametrize("q", ["how do I connect the ultrasonic sensor", "which pins does the ultrasonic sensor use", "explain it in detail"])
def test_wiring_questions_and_questions_without_a_sensor_are_not_calculation_questions(q):
    assert target(q) is None


def test_wiring_sentences_are_removed_from_an_explanation():
    out = strip_wiring("Distance = speed x time / 2.\n- Connect VCC to 5V.\n1. The pulse goes out and back.\nUse GPIO5 for TRIG. The result is in cm.")
    assert "GPIO" not in out and "VCC" not in out and "Distance = speed x time / 2." in out and "The result is in cm." in out


@pytest.mark.asyncio
async def test_the_follow_up_gets_the_formula_and_no_wiring(retriever):  # noqa: F811
    llm = FakeLLM("**Distance from the echo**\n1. The sensor sends a sound pulse and times the echo.\n"
                  "2. distance_cm = echo_time_us x 0.0343 / 2\n3. Connect TRIG to GPIO5 and VCC to 5V.\n4. Example: 1166 us gives about 20 cm.")
    svc = build(retriever, llm, **GENERIC)
    await turn(svc, "what is the ultrasonic sensor", "explain-1")
    d = await turn(svc, "now explain the same concept in detail and how can i read the dsitance value in centimeters", "explain-1")
    assert llm.calls[0][0]["content"] == EXPLAIN_SYSTEM
    notes = llm.calls[0][-1]["content"]
    assert "SENSOR NOTES: Ultrasonic Sensor" in notes and "GPIO" not in notes and "VCC" not in notes
    assert "Get Distance with Echo and Trigger Pin" in notes          # the documented block that returns the value
    assert "distance_cm = echo_time_us" in d["answer"] and "GPIO" not in d["answer"] and "VCC" not in d["answer"]
    assert not d["warnings"]


@pytest.mark.asyncio
async def test_other_sensors_use_the_same_mode(retriever):  # noqa: F811
    llm = FakeLLM("Light level changes the LDR resistance. Vout = Vin x R / (R + R_ldr).")
    d = await turn(build(retriever, llm, **GENERIC), "what is the formula to convert the LDR reading to lux", "explain-2")
    assert llm.calls[0][0]["content"] == EXPLAIN_SYSTEM and "LDR" in llm.calls[0][-1]["content"] and "Vout" in d["answer"]
