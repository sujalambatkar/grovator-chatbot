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
    assert not kb.detect("how do I connect an ultrasonic sensor").strong


@pytest.mark.asyncio
async def test_wiring_is_a_table_built_from_files_with_i2c_pins_and_no_llm(retriever):
    d, llm = await ask(retriever, "How do I connect the sensor?")
    assert all_llm_calls(llm) == 0 and d["tokens_total"] == 0
    rows = {r["sensor_pin"]: r["kit_pin"] for r in d["connection_table"]["rows"]}
    assert rows == {"VIN": "3.3V", "GND": "GND", "SDA": "SDA (GPIO21)", "SCL": "SCL (GPIO22)"}
    assert "RX" not in d["answer"] and "TX" not in d["answer"] and "GPIO21" in d["answer"]
    assert d["images"] and d["sources"]


@pytest.mark.asyncio
async def test_controller_connection_is_not_documented_and_never_mapped(retriever):
    d, llm = await ask(retriever, "How does the pulse oximeter connect to the Grovator controller?")
    assert all_llm_calls(llm) == 0 and d["refused_reason"] == "not_documented"
    assert "don't have that detail yet" in d["answer"] and not d.get("connection_table")


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
    d, _ = await ask(retriever, "What is I2C?", FakeLLM("I2C uses two wires. On your kit use GPIO15 for the clock."))
    assert d["refused_reason"] == "unsupported_pin"


@pytest.mark.asyncio
async def test_other_gateway_sensors_are_not_hijacked(retriever):
    llm = FakeLLM("Connect it to a digital GPIO.")
    d, llm = await ask(retriever, "How do I connect an ultrasonic sensor?", llm)
    assert not d.get("connection_table") and "oximeter" not in d["answer"].lower()


@pytest.mark.asyncio
async def test_followup_keeps_the_application_in_the_query(retriever):
    llm = FakeLLM("The LEDs show the heart rate band.")
    svc = build(retriever, llm, **OVER)
    await turn(svc, "How do I connect the pulse oximeter?", "app-f")
    d = await turn(svc, "why would the program pause for half a second?", "app-f")
    assert "Pulse Oximeter" in d["standalone_query"]
