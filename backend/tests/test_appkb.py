"""Application assistant (backend/knowledge/*): deterministic structured answers, gaps, general mode, health note."""
import pytest
import pytest_asyncio

from app.chat.appkb import AppKB, Entities
from app.llm.prompts import GENERAL_SYSTEM
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from knowledge.loader import KNOWLEDGE_DIR, validate
from tests.conftest import FakeLLM, all_llm_calls
from tests.test_hardware import ON, REAL, STU, build, turn

OVER = dict(app_knowledge_dir=str(KNOWLEDGE_DIR), enable_semantic_cache=False)


@pytest_asyncio.fixture(scope="module")
async def retriever():
    r = HybridRetriever(KnowledgeStore(make_client("memory"), "kb_app"))
    await ingest(REAL, r, app_root=KNOWLEDGE_DIR)
    return r


async def ask(retriever, msg, llm=None, sid="app-1", **over):
    llm = llm or FakeLLM("Fake model answer.")
    svc = build(retriever, llm, **{**OVER, **over})
    return await turn(svc, msg, sid), llm


def test_knowledge_files_validate():
    k = validate()
    assert "pulseoximeter-health-monitoring" in k.applications and len(k.blocks) >= 7


def test_entity_detection_handles_aliases_and_typos():
    kb = AppKB(validate())
    assert kb.detect("how does the oximeter work").sensors == ["pulseoximeter_max30102"]
    assert "pulseoximeter_max30102" in kb.detect("my MAX30102 shows 0").sensors
    assert "pulseoximeter_max30102" in kb.detect("pulse oxymeter wiring").sensors      # typo
    assert "govator_controller_pi" in kb.detect("on the pi controller").kits
    assert kb.detect("how do I connect an ultrasonic sensor").sensors == ["ultrasonic_sensor"]     # the sensor list covers it
    assert not kb.detect("how do I connect a banana to the gateway").strong


@pytest.mark.asyncio
async def test_wiring_is_a_table_built_from_files_with_i2c_pins_and_no_llm(retriever):
    d, llm = await ask(retriever, "How do I connect the sensor?")
    assert all_llm_calls(llm) == 0 and d["tokens_total"] == 0
    rows = {r["sensor_pin"]: r["kit_pin"] for r in d["connection_table"]["rows"]}
    assert rows == {"VIN": "3.3V", "GND": "GND", "SDA": "SDA (GPIO21)", "SCL": "SCL (GPIO22)"}
    assert "RX" not in d["answer"] and "TX" not in d["answer"] and "GPIO21" in d["answer"]
    assert d["images"] and d["sources"]


@pytest.mark.asyncio
async def test_controller_connection_comes_from_the_sensor_list_and_is_never_mapped_to_esp32_gpios(retriever):
    d, llm = await ask(retriever, "How does the pulse oximeter connect to the Grovator controller?")
    assert all_llm_calls(llm) == 0 and d["canned"] == "sensor_connect"
    rows = {r["sensor_pin"]: r["kit_pin"] for r in d["connection_table"]["rows"]}
    assert rows["SDA"] == "any SDA pin of the controller" and rows["SCL"] == "any SCL pin of the controller"
    assert "GPIO21" not in d["answer"].replace("(21, 22)", "") and "do not apply" in d["answer"]
    d2, _ = await ask(retriever, "How does the LED board connect to the Grovator controller?")      # the LED module row of the sensor list
    assert d2["canned"] == "sensor_connect" and any(r["sensor_pin"] == "D0" for r in d2["connection_table"]["rows"])


@pytest.mark.asyncio
async def test_block_stack_order_and_verbatim_code(retriever):
    d, _ = await ask(retriever, "Which blocks do I need?")
    assert [b["title"] for b in d["block_stack"]] == ["Use [Pulseoximeter(Max30102)]", "Read PulseOximeter Data", "Store Heart rate and Oximeter Data"]
    d2, _ = await ask(retriever, "show me the code of the read pulseoximeter data block")
    assert "def get_heart_rate_spo2():" in d2["code_blocks"][0]["code"] and "30000 / avg_interval" in d2["code_blocks"][0]["code"]


@pytest.mark.asyncio
async def test_led3_typo_is_answered_per_the_code(retriever):
    d, llm = await ask(retriever, "why does it print Low - LED 3 OFF when led 3 turns on?")
    assert "typing mistake" in d["answer"] and "turns LED 3 ON" in d["answer"] and all_llm_calls(llm) == 0


@pytest.mark.asyncio
async def test_thresholds_and_zero_reading_faq(retriever):
    d, _ = await ask(retriever, "what are the thresholds for the pulse oximeter")
    assert "95" in d["answer"] and "90" in d["answer"] and "80" in d["answer"] and d["connection_table"]["rows"]
    d2, llm = await ask(retriever, "my sensor shows 0")
    assert "Place Your Finger" in d2["answer"] and all_llm_calls(llm) == 0


@pytest.mark.asyncio
async def test_spo2_is_never_called_accurate_and_health_note_is_added(retriever):
    d, _ = await ask(retriever, "is the pulse oximeter reading accurate?")
    assert "not a medical device" in d["answer"].lower() or "not a medical device" in d["answer"]
    d2, _ = await ask(retriever, "what are normal oxygen levels in the lesson")
    assert "learning project" in d2["answer"]


@pytest.mark.asyncio
async def test_general_question_uses_model_knowledge_without_retrieval(retriever):
    llm = FakeLLM("SpO2 is the percent of your blood carrying oxygen.")
    d, llm = await ask(retriever, "What does SpO2 mean?", llm)
    assert d["intent"] == "general_tech" and d["grounded"] is False and not d["sources"]
    assert llm.calls[0][0]["content"] == GENERAL_SYSTEM and "CONTEXT" not in llm.calls[0][-1]["content"]
    assert "learning project" in d["answer"] and not llm.router_calls


@pytest.mark.asyncio
async def test_general_answer_naming_a_pin_is_blocked(retriever):
    d, _ = await ask(retriever, "What is I2C?", FakeLLM("I2C uses two wires. On your kit use GPIO3 for the clock."))
    assert d["refused_reason"] == "unsupported_pin"


@pytest.mark.asyncio
async def test_other_sensors_get_their_own_wiring_for_both_kits_not_the_lessons(retriever):
    d, llm = await ask(retriever, "How do I connect the ultrasonic sensor?")
    pins = {(r["sensor_pin"], r["note"]) for r in d["connection_table"]["rows"]}
    assert ("TRIG", "Grovator controller") in pins and ("TRIG", "ESP32 kit") in pins and "oximeter" not in d["answer"].lower() and not llm.calls


@pytest.mark.asyncio
async def test_followup_keeps_the_application_in_the_query(retriever):
    llm = FakeLLM("The LEDs show the heart rate band.")
    svc = build(retriever, llm, **OVER)
    await turn(svc, "How do I connect the pulse oximeter?", "app-f")
    d = await turn(svc, "why would the program pause for half a second?", "app-f")
    assert "Pulse Oximeter" in d["standalone_query"]



@pytest.mark.asyncio
async def test_grounded_question_after_router_keeps_working(retriever):
    """Regression: a variable named `recent` was shadowed by the router's list, so any non-deterministic question crashed."""
    d, llm = await ask(retriever, "What is the challenge in this lesson?")
    assert d["answer"].startswith("Fake model answer") and llm.calls


@pytest.mark.asyncio
async def test_i2c_address_is_not_a_personal_address(retriever):
    d, llm = await ask(retriever, "What is the I2C address of the MAX30102?")
    assert d.get("refused_reason") is None and "personal details" not in d["answer"] and "0x57" in d["answer"]


@pytest.mark.asyncio
async def test_lesson_leds_win_over_the_generic_gateway_led(retriever):
    d, llm = await ask(retriever, "Which GPIO is LED 2 connected to?")
    assert "GPIO23" in d["answer"] and "medium heart rate" in d["answer"].lower() and not llm.calls
    d2, llm2 = await ask(retriever, "How do I connect an LED to the gateway?", FakeLLM("Use a digital GPIO."))
    assert "oximeter" not in d2["answer"].lower() and "GPIO23" not in d2["answer"]


@pytest.mark.asyncio
async def test_expected_output_comes_from_the_program(retriever):
    d, llm = await ask(retriever, "What is the expected serial output when I place my finger?")
    assert "Heartrate :" in d["answer"] and "Normal oxygen level" in d["answer"] and not llm.calls


@pytest.mark.asyncio
async def test_led_followup_stays_on_the_lesson_not_the_gateway_led(retriever):
    llm = FakeLLM("The LEDs show the heart rate band.")
    svc = build(retriever, llm, **OVER)
    await turn(svc, "How do I connect the pulse oximeter?", "app-led")
    d = await turn(svc, "what about the LED?", "app-led")
    assert "Pulse Oximeter" in d["standalone_query"]


@pytest.mark.asyncio
async def test_questions_on_an_open_gateway_device_page_are_about_that_sensor_not_the_application(retriever):
    svc = build(retriever, FakeLLM("VCC goes to 5V."), **OVER)
    d = await turn(svc, "Where does the VCC wire go?", "gw-page-1", page="devices/ultrasonic_sensor")
    assert {r["sensor_pin"] for r in d["connection_table"]["rows"]} >= {"VCC", "TRIG", "ECHO"} and "oximeter" not in d["answer"].lower()
    d2 = await turn(svc, "How do I connect the pulse oximeter?", "gw-page-2", page="devices/ultrasonic_sensor")   # naming the application still works
    assert any(r["kit_pin"] == "SDA (GPIO21)" for r in d2["connection_table"]["rows"])
    d3 = await turn(svc, "What are the wiring rules?", "gw-page-3", page="devices/ultrasonic_sensor")
    assert d3.get("canned") == "pin_rules"


@pytest.mark.asyncio
async def test_sensor_facts_and_code_statements_are_looked_up_not_searched(retriever):
    d, llm = await ask(retriever, "What is the I2C address of the MAX30102?")
    assert "0x57" in d["answer"] and not llm.calls
    d, llm = await ask(retriever, "What baud rate does the V2.0 sensor use?")
    assert "9600" in d["answer"] and not llm.calls
    d, llm = await ask(retriever, "What does led_1.on() do?")
    assert "Turns ON LED 1" in d["answer"] and not llm.calls
    d, llm = await ask(retriever, "what does time.sleep(0.5) mean")
    assert "Waits" in d["answer"] and not llm.calls


@pytest.mark.asyncio
async def test_comparison_of_ideas_is_general_even_if_a_gateway_device_is_named(retriever):
    d, llm = await ask(retriever, "What is the difference between a sensor and an LED?", FakeLLM("A sensor measures; an LED shows light."))
    assert d["intent"] == "general_tech" and llm.calls[0][0]["content"] == GENERAL_SYSTEM


@pytest.mark.asyncio
@pytest.mark.parametrize("q", ["How do I create this application?", "How do I build this project step by step?", "What sensors are required?", "what do i need to make it"])
async def test_build_guide_has_parts_connections_and_numbered_steps(retriever, q):
    d, llm = await ask(retriever, q)
    assert not llm.calls and d["canned"] == "build_guide"
    a = d["answer"]
    assert "pulse oximeter" in a.lower() and "ESP32" in a and "LED board" in a                 # required parts
    assert {r["sensor_pin"]: r["kit_pin"] for r in d["connection_table"]["rows"]}["SDA"] == "SDA (GPIO21)"   # connections
    assert "1. Get the parts" in a and "Circuit Designer" in a and "Block Designer" in a and "7. Run" in a   # steps
    assert [b["order"] for b in d["block_stack"]] == [1, 2, 3] and d["images"]


@pytest.mark.asyncio
@pytest.mark.parametrize("q,must", [
    ("How do I connect the alcohol sensor?", ["DO to any digital GPIO pin", "ESP32 kit"]),
    ("what is a flame sensor?", ["infrared"]),
    ("How do I connect the DHT11?", ["D0", "ESP32 kit"]),
    ("how do i connect the gps sensor", ["Rx", "Tx", "UART"]),
    ("Which pins support analog input?", ["GPIO32", "AI1"]),
    ("which sensors use I2C", ["Accelerometer", "LCD Display", "Pulseoximeter"]),
    ("List all sensors", ["Servo Motor", "Water Pump", "Weight Sensor"]),
    ("How do I connect the stepper motor on the Grovator controller?", ["C1", "1B"]),
    ("how do i connect the rtc", ["no pins are given"]),
])
async def test_catalog_questions_are_answered_from_the_files(retriever, q, must):
    d, llm = await ask(retriever, q)
    text = d["answer"] + str(d.get("connection_table"))
    assert not llm.calls, d["answer"]
    for m in must:
        assert m in text, (q, m, d["answer"])


@pytest.mark.asyncio
async def test_troubleshooting_on_another_sensors_page_is_not_the_lessons_faq(retriever):
    svc = build(retriever, FakeLLM("Check the wires."), **OVER)
    d = await turn(svc, "What if it is not working?", "gw-page-4", page="devices/ultrasonic_sensor")
    assert d.get("canned") is None and "Place Your Finger" not in d["answer"]
