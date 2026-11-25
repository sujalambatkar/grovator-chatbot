"""The live knowledge base (backend/knowledge) is generic: sensors, kits and blocks, no application / lesson text."""
import pytest
import pytest_asyncio

from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from knowledge.loader import validate
from tests.conftest import FakeLLM, all_llm_calls
from tests.test_appkb import GENERIC, GENERIC_DIR
from tests.test_hardware import REAL, build, turn


@pytest_asyncio.fixture(scope="module")
async def retriever():
    r = HybridRetriever(KnowledgeStore(make_client("memory"), "kb_generic"))
    await ingest(REAL, r, app_root=GENERIC_DIR)
    return r


async def ask(retriever, msg, llm=None):
    llm = llm or FakeLLM("Fake model answer.")
    d = await turn(build(retriever, llm, **GENERIC), msg, "gen-kb-1")
    return d, llm


def test_live_knowledge_is_generic():
    k = validate(GENERIC_DIR)
    assert not k.applications and len(k.sensors) >= 55 and len(k.blocks) >= 7 and set(k.kits) == {"esp32_kit", "govator_controller_pi"}
    text = " ".join(p.read_text(encoding="utf-8").lower() for d in ("sensors", "kits", "blocks") for p in (GENERIC_DIR / d).glob("*.yaml"))
    assert "lesson" not in text and "led_1" not in text and "challenge" not in text   # no seeded application text


@pytest.mark.asyncio
@pytest.mark.parametrize("q,must", [
    ("How do I connect the pulse oximeter to the ESP32 kit?", ["SDA (GPIO21)", "3.3V"]),
    ("How do I connect the pulse oximeter to the Grovator controller?", ["any SDA pin"]),
    ("Which blocks do I need for the pulse oximeter?", ["Use [Pulseoximeter(Max30102)]", "Read PulseOximeter Data", "Store Heart rate and Oximeter Data"]),
    ("Show me the code of the read pulseoximeter data block", ["get_heart_rate_spo2"]),
    ("Which blocks do I need?", ["Block code is documented for these sensors"]),
    ("What is the I2C address of the MAX30102?", ["0x57"]),
    ("How does the block code find a heartbeat?", ["30000"]),
    ("How do I connect the alcohol sensor?", ["Grovator controller", "ESP32 kit"]),
    ("Which sensors use I2C?", ["Accelerometer", "BME Sensor"]),
    ("Which pins support analog input?", ["GPIO36", "AI1"]),
])
async def test_generic_questions_answered_from_the_files(retriever, q, must):
    d, llm = await ask(retriever, q)
    text = d["answer"] + str(d.get("connection_table")) + str(d.get("code_blocks")) + str(d.get("block_stack"))
    assert not llm.calls, d["answer"]
    for m in must:
        assert m in text, (q, m, d["answer"])


@pytest.mark.asyncio
async def test_an_unknown_device_gets_no_invented_wiring(retriever):
    d, llm = await ask(retriever, "How do I connect a thermistor to the kit?", FakeLLM("I don't have that detail yet."))
    assert not d.get("connection_table") and "SDA (GPIO21)" not in d["answer"]


@pytest.mark.asyncio
async def test_worry_about_a_reading_gets_the_safety_reply_without_a_model_call(retriever):
    d, llm = await ask(retriever, "My oxygen reading is 90. Should I be worried?")
    assert not llm.calls and ("trusted adult" in d["answer"] or "professional" in d["answer"])


@pytest.mark.asyncio
async def test_weight_size_price_questions_say_not_documented(retriever):
    d, llm = await ask(retriever, "What is the weight of the sensor?")
    assert not llm.calls and "don't have that detail" in d["answer"] and d["refused_reason"] == "not_documented"
    d2, llm2 = await ask(retriever, "How do I connect the weight sensor?")       # the Weight Sensor itself is still a sensor
    assert d2["canned"] == "sensor_connect"
