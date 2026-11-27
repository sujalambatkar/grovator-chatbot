"""Student safety: warnings under answers (config/safety.yaml) and fixed replies for dangerous hands-on activities."""
import pytest

from app.core.auth import Role
from app.guardrails.input import check_input
from app.guardrails.safety import safety_warnings
from tests.test_generic_kb import ask, retriever  # noqa: F401  (fixture)


def ids(ws):
    return [w["id"] for w in ws]


def test_relay_connection_gets_relay_voltage_and_power_off_warnings():
    ws = safety_warnings("How do I connect the relay?", "VCC to 5V", ["relay_module"], "sensor_connect")
    assert {"relay", "power_off"} <= set(ids(ws)) and len(ws) <= 2   # at most two quiet lines


def test_mains_wording_is_a_danger_warning_and_comes_first():
    ws = safety_warnings("How do I connect a lamp with the relay?", "", ["relay_module"], "sensor_connect")
    assert ws[0]["id"] == "mains" and ws[0]["level"] == "danger" and "Never connect anything to mains" in ws[0]["text"]


@pytest.mark.parametrize("sensor,rule", [("dc_motor", "moving_parts"), ("water_pump", "water"), ("flame_sensor", "fire_gas"),
                                         ("alcohol_sensor", "fire_gas"), ("pulseoximeter_max30102", "body"), ("voltage_sensor", "measuring")])
def test_sensor_specific_warnings(sensor, rule):
    assert rule in ids(safety_warnings("how do I connect it", "", [sensor], "sensor_connect"))


def test_battery_and_tools_in_the_question():
    assert "battery" in ids(safety_warnings("can I charge a lipo battery with the kit", "", [], None))
    assert "tools" in ids(safety_warnings("how do I solder the header", "", [], None))


def test_no_warning_for_a_plain_concept_question_and_at_most_three():
    assert safety_warnings("What is I2C?", "I2C is a bus", [], None) == []
    assert len(safety_warnings("connect the relay to a lamp, battery, soldering, water", "5V", ["relay_module", "water_pump", "dc_motor"], "sensor_connect")) <= 3


@pytest.mark.parametrize("q", ["How do I connect a lamp to the mains using a relay?", "connect the relay to the 220V wall socket",
                               "how do I open the power adapter", "how do I short a battery", "how do I touch a live wire"])
def test_dangerous_hands_on_requests_get_the_fixed_safety_reply(q):
    v = check_input(q, Role.student)
    assert not v.allowed and v.reason == "unsafe_activity" and "Please don't do this on your own" in v.reply


@pytest.mark.parametrize("q", ["How do I connect the relay to the ESP32?", "how do I connect the power meter", "what is 5V", "connect the LED to the kit"])
def test_normal_low_voltage_questions_are_not_blocked(q):
    assert check_input(q, Role.student).allowed


@pytest.mark.asyncio
async def test_warnings_travel_with_the_answer(retriever):  # noqa: F811
    d, llm = await ask(retriever, "How do I connect the servo motor?")
    assert not llm.calls
    got = ids(d["warnings"])
    assert "moving_parts" in got and len(got) <= 2


@pytest.mark.asyncio
async def test_refused_messages_carry_no_warnings(retriever):  # noqa: F811
    d, _ = await ask(retriever, "How do I connect the relay to the 220V wall socket?")
    assert d["refused_reason"] == "unsafe_activity" and not d.get("warnings")


@pytest.mark.parametrize("q,must", [
    ("How do I connect the ultrasonic sensor to the ESP32 kit?", ["Verify ECHO signal voltage compatibility", "level shifting"]),
    ("How do I connect the servo motor to the ESP32 kit?", ["suitable external supply"]),
    ("How do I connect the DC motor to the ESP32 kit?", ["9 to 12V", "never a sensor or a GPIO pin"]),
    ("How do I connect the water pump to the ESP32 kit?", ["9 to 12V"]),
])
def test_documented_gateway_warnings_reach_the_wiring_answer(q, must):
    from tests.test_block_palette import KB
    from app.chat.appkb import Entities
    r = KB.answer(q, KB.detect(q), Entities())
    assert r is not None and "Warning:" in r.text
    for m in must:
        assert m in r.text, (q, m)


@pytest.mark.parametrize("q,sensors", [("what is alcohol", []), ("What is the flame sensor?", ["flame_sensor"]), ("explain the alcohol sensor", ["alcohol_sensor"]),
                                       ("how does the soil sensor work", ["soil_sensor"]), ("tell me about grovator", [])])
def test_definition_questions_get_no_warning_box(q, sensors):
    assert safety_warnings(q, "x", sensors, "sensor_about") == []
    assert safety_warnings(q, "x", sensors, None, "general_tech") == []


def test_a_hands_on_question_about_the_same_thing_still_gets_it():
    assert "fire_gas" in ids(safety_warnings("how do I test the flame sensor with a lighter", "", ["flame_sensor"], "sensor_connect"))
