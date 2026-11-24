"""Pre-written answers: generated from the dataset, matched strictly, zero LLM tokens."""
import copy
import re

import pytest
import pytest_asyncio

from app.chat.canned import CannedAnswers
from app.rag.hardware import Gateway, pretty
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from tests.conftest import FakeLLM
from tests.test_hardware import DATA, GW, REAL, STU, build, context_of, turn

C = CannedAnswers(GW)
ONN = dict(enable_canned=True)


@pytest_asyncio.fixture(scope="module")
async def real_retriever():
    r = HybridRetriever(KnowledgeStore(make_client("memory"), "kb_real_canned"))
    await ingest(REAL, r)
    return r


def m(msg, focus=()):
    return C.match(msg, GW.detect(msg), list(focus))


# ---------------------------------------------------------------------------------------------- built from the data
def test_every_device_has_a_connect_and_a_pins_answer_made_of_its_own_records():
    for key, dev in DATA["devices"].items():
        a = C.connect(key)
        assert a.kind == "connect" and pretty(key) in a.text and a.sources == [f"{pretty(key)}: Connection Guide"]
        for step in GW.device_steps(key):
            assert step in a.text
        for pin, target in dev.get("pins", {}).items():
            assert pin in a.text and (target in a.text or "digital gpio" in target.lower())   # "Digital GPIO" is spelled out as the connector's Signal pin
        pa = C.pins(key)
        for pin, target in dev.get("pins", {}).items():
            assert pin in pa.text
        if dev.get("signal"):
            assert dev["signal"] in pa.text


def test_canned_answers_never_contain_a_gpio_the_dataset_does_not_have():
    known = set(re.findall(r"GPIO\d+", str(DATA)))
    answers = [C.connect(k) for k in DATA["devices"]] + [C.pins(k) for k in DATA["devices"]] + [C.list_devices(), C.gateway(), C.rules()]
    answers += [C.interface(i) for i in DATA["interfaces"]] + [a for k in DATA["devices"] if (a := C.what(k))]
    for a in answers:
        assert set(re.findall(r"GPIO\d+", a.text)) <= known, a.text


def test_what_is_is_answered_only_when_the_dataset_defines_the_device():
    assert "estimates distance" in C.what("ultrasonic_sensor").text and "GND" in C.what("ultrasonic_sensor").text
    assert C.what("buzzer") is None and C.what("servo") is None          # no definition in the data: a template must not make one up
    assert m("what is a buzzer") is None                                # so the model answers (from the glossary + general knowledge)
    assert m("what is an ultrasonic sensor").kind == "what"


def test_nothing_is_hardcoded_a_new_device_gets_answers_automatically():
    data = copy.deepcopy(DATA)
    data["devices"]["gas_sensor"] = {"interface": "digital", "signal": "AO7", "connect_to": "Available digital GPIO"}
    c = CannedAnswers(Gateway(data))
    a = c.match("how do I connect the gas sensor", ["gas_sensor"], [])
    assert a and "AO7" in a.text and "Gas Sensor" in a.text


# ---------------------------------------------------------------------------------------------- strict matching
@pytest.mark.parametrize("msg,kind", [
    ("How do I connect the ultrasonic sensor?", "connect"), ("how to connect ultrasonic sensor", "connect"), ("connect the DHT11", "connect"),
    ("wiring of the servo", "connect"), ("How do I wire the I2C LCD", "connect"), ("What is an ultrasonic sensor?", "what"),
    ("what's the dht11", "what"), ("how does an ultrasonic sensor work", "what"), ("Which pins does the I2C LCD use?", "pins"),
    ("what pins does the servo use", "pins"), ("Which sensors can I connect?", "list"), ("what is the gateway", "gateway"),
    ("Which pins support analog input?", "interface"), ("which pins are digital GPIO", "interface"), ("Which pins are I2C", "interface"),
    ("what are the wiring rules", "rules"),
])
def test_plain_questions_get_a_prewritten_answer(msg, kind):
    a = m(msg)
    assert a is not None and a.kind == kind, msg


@pytest.mark.parametrize("msg", [
    "I connected TRIG to GPIO5, what next?",                       # the student's own wiring
    "How do I connect the ultrasonic sensor and what do I do next?",   # two questions in one
    "How do I connect the ultrasonic sensor? Which pins does it use?",
    "buzzer vs servo wiring", "What is the difference between the buzzer and the servo?", "how do I connect the buzzer and the servo",
    "why is my ultrasonic sensor not working", "What if the ultrasonic sensor is not working?", "what happens if I connect the servo to the wrong pins",
    "Can I connect an IR sensor to the gateway?", "Is it safe to connect a 5V signal to the ultrasonic sensor",
    "Where does the VCC wire of the ultrasonic sensor go?", "what code do I upload for the ultrasonic sensor",
    "I have 2 ultrasonic sensors, how do I connect them", "How do I connect my ultrasonic sensor to the kit I bought",
    "tell me everything about the ultrasonic sensor please and also how the gateway powers it from a battery pack",
    "how do I connect buzzer servo",                               # two devices named
    "How do I connect the sensor?",                                # no device named, none in focus
    "what is a sensor",                                            # a general concept: the glossary/model, not a device template
])
def test_anything_else_falls_through_to_the_model(msg):
    assert m(msg) is None, msg


def test_a_pronoun_uses_the_device_in_focus_but_only_when_exactly_one():
    assert m("how do I connect it", ["servo"]).sources == ["Servo: Connection Guide"]
    assert m("which pins does it use", ["i2c_lcd"]).kind == "pins"
    assert m("what is it", ["ultrasonic_sensor"]).kind == "what"
    assert m("how do I connect it", ["servo", "buzzer"]) is None     # two in focus: the clarifying question handles it
    assert m("how do I connect it", []) is None
    assert m("how do I connect the buzzer", ["servo"]).sources == ["Buzzer: Connection Guide"]   # a named device beats the focus


# ---------------------------------------------------------------------------------------------- in the pipeline
@pytest.mark.asyncio
async def test_a_plain_question_costs_zero_tokens_and_never_calls_the_model(real_retriever):
    llm = FakeLLM("must not be called")
    svc = build(real_retriever, llm, **ONN)
    d = await turn(svc, "How do I connect the ultrasonic sensor?", "cn-sess-1")
    assert d["tokens_total"] == 0 and d["input_tokens"] == 0 and d["grounded"] and d["canned"] == "connect"
    assert d["sources"] == ["Ultrasonic Sensor: Connection Guide"] and "TRIG" in d["answer"] and "Digital GPIO pins" in d["answer"]
    assert not llm.calls and not llm.router_calls and d["naive_tokens"] > 1000


@pytest.mark.asyncio
async def test_the_conversation_continues_after_a_prewritten_answer(real_retriever):
    llm = FakeLLM("Check the datasheet or ask your teacher.")
    svc = build(real_retriever, llm, **ONN)
    d1 = await turn(svc, "How do I connect the ultrasonic sensor?", "cn-sess-2")
    assert d1["canned"] == "connect" and not llm.calls
    d2 = await turn(svc, "What should I do after connecting it?", "cn-sess-2")     # not simple: goes to the model, with the sensor in focus
    assert not d2.get("canned") and llm.calls and "Ultrasonic Sensor" in context_of(llm)
    d3 = await turn(svc, "which pins does it use", "cn-sess-2")                    # pronoun resolves to the device in focus: canned again
    assert d3["canned"] == "pins" and "Ultrasonic" in d3["answer"]


@pytest.mark.asyncio
async def test_what_is_a_buzzer_still_goes_to_the_model(real_retriever):
    llm = FakeLLM("A buzzer makes a sound. On the Grovator ESP32 IoT Gateway it connects to an available digital GPIO.")
    svc = build(real_retriever, llm, **ONN)
    d = await turn(svc, "what is a buzzer", "cn-sess-3")
    assert not d.get("canned") and llm.calls


@pytest.mark.asyncio
async def test_it_can_be_switched_off(real_retriever):
    llm = FakeLLM("Connect VCC to 5V and GND to GND, TRIG and ECHO to the Signal pin of a digital GPIO connector.")
    svc = build(real_retriever, llm, enable_canned=False)
    d = await turn(svc, "How do I connect the ultrasonic sensor?", "cn-sess-4")
    assert not d.get("canned") and llm.calls


# ---------------------------------------------------------------------------------------------- suggested questions
@pytest.mark.asyncio
async def test_every_suggested_question_on_every_page_is_answered_for_zero_tokens(real_retriever):
    """User report: the chat suggested "What should I do after connecting it?" etc., which cost tokens. A suggestion is now offered only if the
    real pipeline answers it with a pre-written answer, checked here end-to-end (page open, model forbidden) for every page."""
    from app.content.catalog import ContentCatalog
    llm = FakeLLM("MODEL MUST NOT BE CALLED")
    svc = build(real_retriever, llm, **ONN)
    pages = ContentCatalog(REAL).visible(STU)
    seen = 0
    for i, doc in enumerate(pages):
        for j, q in enumerate(C.suggestions(doc.source)):
            d = await turn(svc, q, f"cn-sg-{i:03d}-{j}", page=doc.source)
            assert d.get("canned") and d["tokens_total"] == 0, (doc.source, q, d["answer"][:80])
            seen += 1
    assert not llm.calls and not llm.router_calls and seen >= 4 * len(pages) - 12


def test_suggestions_follow_the_data_not_a_fixed_list():
    assert "What is it?" in C.suggestions("devices/ultrasonic_sensor")          # the dataset defines it
    assert "What is it?" not in C.suggestions("devices/buzzer")                # no definition: asking would cost tokens
    assert "Which pins are I2C?" in C.suggestions("interfaces/i2c")
    assert all(len(C.suggestions(s_)) >= 2 for s_ in ("devices/servo", "gateway/overview", "interfaces/keypad", "concepts/glossary", "nonsense/x"))
    for q in sum((C.suggestions(f"devices/{k}") for k in DATA["devices"]), []):
        assert not re.search(r"after|not working|next|code", q, re.I), q


def test_the_content_api_serves_the_suggestions(real_retriever):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.conftest import make_token
    app = create_app(llm=FakeLLM("x"), settings=__import__("app.config", fromlist=["Settings"]).Settings(
        knowledge_dir=str(REAL), usage_log_dir="", enable_canned=True, redis_url="memory", qdrant_url="memory"))
    with TestClient(app) as client:
        r = client.get("/content", headers={"Authorization": f"Bearer {make_token('student')}"})
        docs = {d["source"]: d for d in r.json()["docs"]}
        assert "Which pins does it use?" in docs["devices/servo"]["suggestions"]
        assert "What is it?" not in docs["devices/buzzer"]["suggestions"]
