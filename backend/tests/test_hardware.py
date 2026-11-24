"""The REAL gateway dataset (backend/knowledge_src/hardware/*.json): ingestion, retrieval, sensor focus in conversations,
clarifying questions, the invented-pin guard, and "no dummy content any more".

The other suites run on a synthetic fixture corpus (tests/fixtures/knowledge_synthetic); this file is the one that
runs on what ships.
"""
import copy
import json
from pathlib import Path

import fakeredis.aioredis
import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.chat.cache import SemanticCache
from app.chat.service import ChatService
from app.config import Settings
from app.content.catalog import ContentCatalog
from app.core.abuse import TokenBudget
from app.core.usage import UsageTracker
from app.guardrails.output import unsupported_pins
from app.memory.long_term import MemoryManager, MemoryStore
from app.memory.session import SessionStore
from app.rag.hardware import Gateway, load_gateway, load_hw_docs, pretty
from app.rag.ingest import ingest, load_documents
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from app.tools.mock_adapter import MockAdapter
from tests.conftest import FakeLLM, make_principal

REAL = Path(__file__).resolve().parent.parent / "knowledge_src"
JSON_PATH = next(REAL.rglob("*.json"))
DATA = json.loads(JSON_PATH.read_text())
GW = load_gateway(str(JSON_PATH))
STU = make_principal("student", user_id="s1")
ON = dict(enable_semantic_cache=True, enable_router_shortcut=True, lazy_summary=True, enable_compression=True, enable_canned=False,
          usage_log_dir="", knowledge_dir=str(REAL))


@pytest_asyncio.fixture(scope="module")
async def real_retriever():
    r = HybridRetriever(KnowledgeStore(make_client("memory"), "kb_real"))
    await ingest(REAL, r)
    return r


def build(retriever, llm, **over):
    s = Settings(**{**ON, **over})
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    mm = MemoryManager(MemoryStore(AsyncQdrantClient(location=":memory:"), "m"), llm, s)
    return ChatService(llm, retriever, SessionStore(redis, 600), s, mm, TokenBudget(redis, 10**9, 10**9), MockAdapter(),
                       cache=SemanticCache(redis, s), tracker=UsageTracker(redis, ""), catalog=ContentCatalog(REAL))


async def turn(svc, msg, sid, p=STU, page=None):
    events = [e async for e in svc.events(p, msg, sid, page)]
    await svc.drain()
    return [e for e in events if e["type"] == "done"][0]


def context_of(llm: FakeLLM) -> str:
    """What the model was actually given for the last answer."""
    return llm.calls[-1][-1]["content"]


# ---------------------------------------------------------------------------------------------- ingestion
def test_every_device_record_is_fully_ingested():
    docs = {d.source: d for d in load_hw_docs(REAL)}
    assert set(DATA["devices"]) <= {s.split("/")[-1] for s in docs if s.startswith("devices/")}
    for key, dev in DATA["devices"].items():
        body = docs[f"devices/{key}"].body
        for field in ("signal", "connect_to", "interface", "warning"):
            if field in dev:
                assert dev[field] in body, f"{key}.{field} lost"
        for pin, target in dev.get("pins", {}).items():
            assert pin in body and target in body, f"{key} pin {pin} -> {target} lost"


def test_every_interface_pin_and_rule_is_ingested():
    text = "\n".join(d.body for d in load_hw_docs(REAL))
    for rec in DATA["interfaces"].values():
        for v in rec.values():
            for item in (v if isinstance(v, list) else [v]):
                assert str(item).rstrip(".") in text, f"lost {item}"
    for rule in DATA["wiring_rules"]:
        assert rule in text
    for concept in DATA["concepts"].values():
        assert concept in text


def test_relationships_device_page_carries_its_interface_and_interface_lists_devices():
    docs = {d.source: d for d in load_hw_docs(REAL)}
    assert "GPIO5" in docs["devices/ultrasonic_sensor"].body            # gateway digital pins travel with the device
    assert "GPIO21" in docs["devices/i2c_lcd"].body and "GPIO22" in docs["devices/i2c_lcd"].body
    assert "GPIO33" in docs["devices/keypad"].body                      # keypad: internal GPIO from the keypad interface record
    assert "Ultrasonic Sensor" in docs["interfaces/digital_gpio"].body and "Servo" in docs["interfaces/digital_gpio"].body
    assert "I2C LCD" in docs["interfaces/i2c"].body and "Pulse Oximeter" in docs["interfaces/i2c"].body


def test_device_page_only_lists_rules_that_apply_to_its_interface():
    docs = {d.source: d for d in load_hw_docs(REAL)}
    assert "I2C devices connect to SDA GPIO21" in docs["devices/i2c_lcd"].body
    assert "I2C devices connect to SDA GPIO21" not in docs["devices/buzzer"].body
    assert "GPIO32 can be used for digital or analog input" in docs["devices/buzzer"].body
    assert "GPIO32 can be used for digital or analog input" not in docs["devices/i2c_lcd"].body


def test_gaps_are_stated_not_filled_in():
    body = {d.source: d.body for d in load_hw_docs(REAL)}["devices/ultrasonic_sensor"]
    assert "Not in the dataset" in body and "example code" in body and "datasheet" in body
    assert "water pump" not in body.lower()
    assert "ECHO" in body and "Verify ECHO signal voltage" in body       # the one documented warning is kept


def test_nothing_is_hardcoded_a_new_device_in_the_json_appears_everywhere():
    data = copy.deepcopy(DATA)
    data["devices"]["gas_sensor"] = {"interface": "digital", "signal": "AO7", "connect_to": "Available digital GPIO"}
    gw = Gateway(data)
    doc = {d.source: d for d in gw.docs()}["devices/gas_sensor"]
    assert "AO7" in doc.body and "Gas Sensor" in doc.title and "GPIO5" in doc.body
    assert gw.detect("how do I wire the gas sensor?") == ["gas_sensor"]
    assert "Gas Sensor" in {d.source: d for d in gw.docs()}["interfaces/digital_gpio"].body


@pytest.mark.parametrize("text,expected", [
    ("How do I connect the ultrasonic sensor to the kit?", ["ultrasonic_sensor"]),
    ("wire my DHT11 and a servo", ["dht11", "servo"]),
    ("the LCD shows nothing", ["i2c_lcd"]),
    ("IR sensor please", ["ir_sensor"]),
    ("can the buzzer share a pin with the LED module", ["buzzer", "led_module"]),
    ("dc motor or stepper motor?", ["dc_motor", "stepper_motor"]),
    ("hall effect sensors", ["hall_effect_sensor"]),
    ("what is a sensor?", []),
    ("which pins are digital?", []),
])
def test_device_names_are_recognised_from_the_data(text, expected):
    assert GW.detect(text) == expected


def test_pretty_names():
    assert pretty("dht11") == "DHT11" and pretty("i2c_lcd") == "I2C LCD" and pretty("ldr_module") == "LDR Module"


# ---------------------------------------------------------------------------------------------- no dummy content
@pytest.mark.asyncio
async def test_old_dummy_dataset_is_gone(real_retriever):
    chunks = load_documents(REAL)
    text = "\n".join(c.text for c in chunks).lower()
    for dummy in ("robotics foundation", "line following", "connect kit", "numbered port", "temperature sensor on port", "iot foundations",
                  "block coding", "status light", "update firmware", "workspace"):
        assert dummy not in text, f"old dummy content still present: {dummy!r}"
    assert not any(c.source.endswith(".md") for c in chunks)
    cat = ContentCatalog(REAL)
    assert {d.category for d in cat.visible(STU)} <= {"gateway", "interfaces", "devices", "concepts"}


@pytest.mark.asyncio
async def test_old_dummy_questions_are_not_answered_from_dummy_docs(real_retriever):
    for q in ("How do I use the Connect Kit tool and the green status light?", "Which lesson comes after IoT Lesson 3?",
              "What does the repeat block do?"):
        res = await real_retriever.retrieve(q, STU, k=4)
        assert not res.hits or res.confidence < Settings().min_retrieval_score or all(h.source.split("/")[0] in
               {"gateway", "interfaces", "devices", "concepts"} for h in res.hits)


# ---------------------------------------------------------------------------------------------- retrieval
@pytest.mark.asyncio
async def test_every_device_is_retrieved_for_its_own_connection_question(real_retriever):
    misses = []
    for key in DATA["devices"]:
        res = await real_retriever.retrieve(f"How do I connect the {pretty(key)} to the gateway?", STU, k=4)
        if f"devices/{key}" not in [h.source for h in res.hits]:
            misses.append(key)
    assert not misses, misses


@pytest.mark.asyncio
async def test_connection_question_retrieves_wiring_and_gateway_pins_together(real_retriever):
    q = "Where does the VCC wire go on the ultrasonic sensor? (Ultrasonic Sensor)"
    res = await real_retriever.retrieve(q, STU, k=4)
    assert "devices/ultrasonic_sensor" in [h.source for h in res.hits]          # the open search finds the device ...
    from app.rag.embeddings import embed_query
    res = await real_retriever.retrieve_from(["devices/ultrasonic_sensor"], STU, await embed_query(q), per_source=2, prefer=["How to wire"])
    text = " ".join(h.text for h in res.hits)                                   # ... and the direct lookup reads its wiring section
    assert "VCC to 5V" in text and "GPIO5" in text and len(res.hits) == 2
    res = await real_retriever.retrieve("Which GPIO pins can I use for a digital sensor?", STU, k=4)
    assert "GPIO5" in " ".join(h.text for h in res.hits)
    res = await real_retriever.retrieve("what is the I2C SDA pin", STU, k=4)
    assert "GPIO21" in " ".join(h.text for h in res.hits)


# ---------------------------------------------------------------------------------------------- conversation
SENSOR_REPLY = ("Connect VCC to 5V, GND to GND, TRIG to the Signal pin of an available digital GPIO connector and ECHO to the Signal pin "
                "of another one. Verify ECHO signal voltage compatibility before connecting to an ESP32 GPIO.")


@pytest.mark.asyncio
async def test_follow_ups_keep_the_sensor_in_focus(real_retriever):
    llm = FakeLLM(SENSOR_REPLY)
    svc = build(real_retriever, llm)
    d = await turn(svc, "How do I connect an ultrasonic sensor?", "hw-sess-1")
    assert d["grounded"] and "TRIG to the Signal pin" in context_of(llm)
    for msg, must in [("What should I do after connecting it?", "Not in the dataset"),
                      ("Where do I connect the VCC wire?", "VCC to 5V"),
                      ("What if it is not working?", "Troubleshooting (documented checks only)")]:
        d = await turn(svc, msg, "hw-sess-1")
        ctx = context_of(llm)
        assert "Ultrasonic Sensor" in ctx and must in ctx, (msg, ctx[:300])
        assert "Buzzer" not in ctx and "Servo" not in ctx, msg      # a neighbouring sensor must not hijack the follow-up


@pytest.mark.asyncio
async def test_switching_sensor_switches_the_focus(real_retriever):
    llm = FakeLLM(SENSOR_REPLY)
    svc = build(real_retriever, llm)
    await turn(svc, "How do I connect an ultrasonic sensor?", "hw-sess-2")
    await turn(svc, "and how do I connect the I2C LCD?", "hw-sess-2")
    await turn(svc, "where does the SDA wire go?", "hw-sess-2")
    ctx = context_of(llm)
    assert "I2C LCD" in ctx and "GPIO21" in ctx and "Ultrasonic" not in ctx


@pytest.mark.asyncio
async def test_vague_sensor_question_gets_a_clarifying_question_not_a_guess(real_retriever):
    llm = FakeLLM("should not be called")
    svc = build(real_retriever, llm)
    d = await turn(svc, "How do I connect the sensor?", "hw-sess-3")
    assert d["refused_reason"] == "needs_clarification" and "Which sensor" in d["answer"]
    assert not llm.calls and not llm.router_calls and d["tokens_total"] == 0
    d = await turn(svc, "the ultrasonic sensor", "hw-sess-3")           # the answer to the question then continues the chat
    assert d["grounded"] and llm.calls


@pytest.mark.asyncio
async def test_two_sensors_in_play_and_an_ambiguous_it_asks_which_one(real_retriever):
    llm = FakeLLM(SENSOR_REPLY)
    svc = build(real_retriever, llm)
    await turn(svc, "Can I connect the buzzer and the servo at the same time?", "hw-sess-4")
    n = len(llm.calls)
    d = await turn(svc, "where does it connect?", "hw-sess-4")
    assert d["refused_reason"] == "needs_clarification" and "Buzzer" in d["answer"] and "Servo" in d["answer"]
    assert len(llm.calls) == n
    d = await turn(svc, "the servo one", "hw-sess-4")
    assert d["grounded"]


@pytest.mark.asyncio
async def test_a_general_question_is_not_mistaken_for_a_vague_one(real_retriever):
    llm = FakeLLM("A sensor detects a physical condition and produces a signal representing it.")
    svc = build(real_retriever, llm)
    d = await turn(svc, "What is a sensor and which sensors can I connect to the gateway?", "hw-sess-5")
    assert d.get("refused_reason") != "needs_clarification"


@pytest.mark.asyncio
async def test_information_missing_from_the_dataset_is_admitted(real_retriever):
    llm = FakeLLM("I don't have that information.")
    svc = build(real_retriever, llm)
    await turn(svc, "What is the maximum current of the servo?", "hw-sess-6")
    ctx = context_of(llm) if llm.calls else ""
    assert "amp" not in ctx.lower().replace("example", "")              # nothing about current limits exists to be quoted


# ---------------------------------------------------------------------------------------------- invented pins
def test_unsupported_pins_unit():
    kb = "digital GPIO pins are GPIO5, GPIO4, GPIO19. SDA GPIO21."
    assert unsupported_pins("Use GPIO5 for TRIG and GPIO21 for SDA.", kb) == []
    assert unsupported_pins("Use GPIO7 for TRIG.", kb) == ["GPIO7"]
    assert unsupported_pins("Use gpio 33.", kb) == ["GPIO33"]
    # a pin the user named may be discussed only when the sentence says it is not supported
    assert unsupported_pins("GPIO7 is not a documented pin.", kb, "can I use GPIO7?") == []
    assert unsupported_pins("Yes, use GPIO7.", kb, "can I use GPIO7?") == ["GPIO7"]


@pytest.mark.asyncio
async def test_an_invented_pin_never_reaches_the_student(real_retriever):
    llm = FakeLLM("Connect the TRIG pin to GPIO7 and the ECHO pin to GPIO8 on the gateway.")
    svc = build(real_retriever, llm)
    d = await turn(svc, "Which pins should I connect the ultrasonic sensor to?", "hw-sess-7")
    assert d["refused_reason"] == "unsupported_pin" and "GPIO7" not in d["answer"] and not d["grounded"]


@pytest.mark.asyncio
async def test_documented_pins_pass_through(real_retriever):
    llm = FakeLLM("The I2C LCD uses SDA on GPIO21 and SCL on GPIO22 of the gateway, plus GND and a supply that matches the module voltage.")
    svc = build(real_retriever, llm)
    d = await turn(svc, "Which pins does the I2C LCD use?", "hw-sess-8")
    assert d["grounded"] and "GPIO21" in d["answer"]


def test_the_word_pin_is_not_a_credential():
    """Regression (live run): "no GPIO pin is used twice" became "no GPIO [CREDENTIAL] used twice" because `pin is ...` looked like a PIN code."""
    from app.guardrails.pii import redact
    assert redact("the SDA pin is GPIO21 and no pin is used twice") == "the SDA pin is GPIO21 and no pin is used twice"
    assert redact("the VCC pin = 5V") == "the VCC pin = 5V"
    assert "4821" not in redact("my pin is 4821") and "[CREDENTIAL]" in redact("my pin is 4821")
    assert "hunter2" not in redact("password is hunter2")


def test_device_pages_say_what_the_dataset_does_not_cover():
    body = {d.source: d.body for d in load_hw_docs(REAL)}["devices/ir_sensor"]
    assert "Not described in the dataset" in body and "No cables" in body


@pytest.mark.parametrize("msg,about", [
    ("What should I do after connecting it?", True), ("Where do I connect the VCC wire?", True), ("what if it is not working?", True),
    ("Which pins can it use?", True), ("Which sensors can I connect?", False), ("What is I2C?", False),
    ("What is a sensor?", False), ("List all the modules on the gateway", False),
])
def test_only_continuations_get_the_sensor_added_to_the_search(msg, about):
    """Regression (live run): after talking about the ultrasonic sensor, "Which sensors can I connect?" was answered with only the ultrasonic sensor."""
    assert ChatService._is_about_focus(msg) is about


@pytest.mark.asyncio
async def test_a_pin_the_student_names_finds_what_that_pin_is_for(real_retriever):
    llm = FakeLLM("GPIO21 is not listed as a digital GPIO connector pin.")
    svc = build(real_retriever, llm)
    await turn(svc, "Can I connect the ultrasonic sensor to GPIO21?", "hw-sess-9")
    ctx = context_of(llm)
    assert "SDA" in ctx and "GPIO21" in ctx


@pytest.mark.asyncio
async def test_general_question_after_a_sensor_is_not_forced_back_to_that_sensor(real_retriever):
    llm = FakeLLM("The gateway documents many sensors.")
    svc = build(real_retriever, llm)
    await turn(svc, "How do I connect an ultrasonic sensor?", "hw-sess-10")
    d = await turn(svc, "Which sensors can I connect to the gateway?", "hw-sess-10")
    assert "(Ultrasonic Sensor)" not in (d.get("standalone_query") or "")


@pytest.mark.asyncio
async def test_a_list_question_is_answered_from_the_overview_with_every_device(real_retriever):
    """Regression (live run): "Which sensors can I connect?" listed 3 sensors because only 3 device pages were retrieved."""
    llm = FakeLLM("The gateway documents many sensors and modules.")
    svc = build(real_retriever, llm, enable_compression=False)
    await turn(svc, "Which sensors can I connect to the gateway?", "hw-sess-11")
    ctx = context_of(llm)
    assert "Sensors, actuators and modules the gateway documents" in ctx
    assert all(pretty(k) in ctx for k in ("alcohol_sensor", "camera", "water_pump")), ctx[:600]


class SeqLLM(FakeLLM):
    """Answers from a list, one per answer call."""

    def __init__(self, replies):
        super().__init__(replies[0])
        self.replies = list(replies)

    def _result(self, messages):
        self.reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return super()._result(messages)

    async def answer_stream(self, messages):
        self.calls.append(messages)
        res = self._result(messages)
        yield "delta", res.text
        yield "done", res


@pytest.mark.asyncio
async def test_an_invented_example_pin_gets_one_strict_retry_instead_of_a_dead_end(real_retriever):
    """Live run: "Can I connect the buzzer and the servo together?" -> the model wrote "e.g. GPIO15" and the student got only a refusal."""
    good = "Yes. Each module needs its own Signal pin on an available digital GPIO connector such as GPIO5, GPIO4 or GPIO19; the gateway connector also provides 5V and GND."
    llm = SeqLLM(["Connect the Servo signal to GPIO15 and the Buzzer signal to GPIO16.", good])
    svc = build(real_retriever, llm)
    d = await turn(svc, "Can I connect the buzzer and the servo together?", "hw-sess-12")
    assert len(llm.calls) == 2 and "Do not name any GPIO number" in llm.calls[1][-1]["content"]
    assert d["grounded"] and "GPIO15" not in d["answer"] and "GPIO5" in d["answer"]
    assert d["input_tokens"] > 0 and d["tokens_total"] >= d["input_tokens"]      # both calls are counted


@pytest.mark.asyncio
async def test_fake_citation_markers_are_removed(real_retriever):
    llm = FakeLLM("Connect VCC to 5V and GND to GND on the digital GPIO connector.【1†L9-L10】 Verify the ECHO signal voltage first.【2†L5-L6】")
    svc = build(real_retriever, llm)
    d = await turn(svc, "Where does the ultrasonic sensor VCC go?", "hw-sess-13")
    assert "【" not in d["answer"] and "5V" in d["answer"]


@pytest.mark.asyncio
async def test_naming_the_sensor_reads_only_that_sensors_guide(real_retriever):
    """User report: "i have connected the D0 pin to GPIO, whats next" -> "Which sensor?" -> "for alcohol sensor" cost 958 tokens, searched
    the whole knowledge base and answered from the Flame Sensor page that was open. Now: the question before the name is the real question,
    and only the alcohol sensor's own guide is read."""
    llm = FakeLLM("After wiring, check the datasheet.")
    svc = build(real_retriever, llm)
    FLAME = "devices/flame_sensor"
    d = await turn(svc, "i have connected the module's D0 signal pin to GPIO whats next", "hw-sess-14", page=FLAME)
    assert d["refused_reason"] == "needs_clarification"
    d = await turn(svc, "for alcohol sensor", "hw-sess-14", page=FLAME)
    ctx = context_of(llm)
    assert d["sources"] == ["Alcohol Sensor: Connection Guide"] or "Alcohol Sensor" in ctx
    assert "Flame" not in ctx and "DATA" in ctx                       # the open Flame page must not leak in; the alcohol signal is DATA
    assert "Alcohol Sensor: Connection Guide > After wiring" in ctx                         # the intent ("what's next") picked the right section
    assert d["input_tokens"] < 600, d["input_tokens"]


@pytest.mark.asyncio
async def test_direct_lookup_picks_the_section_the_question_is_about(real_retriever):
    llm = FakeLLM("Check the labels.")
    svc = build(real_retriever, llm)
    await turn(svc, "How do I connect the servo?", "hw-sess-15")
    for msg, heading in [("What if it is not working?", "Servo: Connection Guide > Troubleshooting"), ("what do I do next?", "Servo: Connection Guide > After wiring"),
                         ("what parts do I need?", "Servo: Connection Guide > Components needed")]:
        await turn(svc, msg, "hw-sess-15")
        assert heading in context_of(llm), (msg, context_of(llm)[:200])
    assert len(llm.calls[-1][-1]["content"]) < 2500


@pytest.mark.asyncio
async def test_how_do_i_connect_this_on_a_device_page_means_that_device(real_retriever):
    llm = FakeLLM("Connect it.")
    svc = build(real_retriever, llm)
    await turn(svc, "How do I connect this to the gateway?", "hw-sess-16", page="devices/buzzer")
    assert "Buzzer" in context_of(llm) and "Ultrasonic" not in context_of(llm)


@pytest.mark.asyncio
async def test_what_should_i_do_after_is_not_a_lesson_order_question(real_retriever):
    """Live run: "What should I do after connecting it?" matched the lesson-order rule, searched everything and pulled in the open Flame Sensor page
    (1,013 tokens). With gateway content there are no lessons, and the question stays inside the sensor's own guide."""
    llm = FakeLLM("Check the datasheet.")
    svc = build(real_retriever, llm)
    await turn(svc, "How do I connect an ultrasonic sensor?", "hw-sess-17", page="devices/flame_sensor")
    d = await turn(svc, "What should I do after connecting it?", "hw-sess-17", page="devices/flame_sensor")
    ctx = context_of(llm)
    assert "Flame" not in ctx and "Ultrasonic" in ctx
    assert d["input_tokens"] < 700, d["input_tokens"]


# ---------------------------------------------------------------------------------------------- token optimisations (direct lookups)
@pytest.mark.asyncio
async def test_direct_lookup_sends_no_chat_history_but_a_self_contained_question(real_retriever):
    llm = FakeLLM("Connect VCC to 5V.")
    svc = build(real_retriever, llm)
    await turn(svc, "How do I connect an ultrasonic sensor?", "hw-sess-20")
    await turn(svc, "Where do I connect the VCC wire?", "hw-sess-20")
    msgs = llm.calls[-1]
    assert len(msgs) == 2                                                  # system + one user block: no replayed turns
    assert "QUESTION: Where do I connect the VCC wire? (Ultrasonic Sensor)" in msgs[-1]["content"]
    assert "CONVERSATION SUMMARY" not in msgs[-1]["content"]


def _shared_service(real_retriever, llm):
    """Two students, same school, one shared cache (as in production)."""
    s = Settings(**ON)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    mm = MemoryManager(MemoryStore(AsyncQdrantClient(location=":memory:"), "m"), llm, s)
    return ChatService(llm, real_retriever, SessionStore(redis, 600), s, mm, TokenBudget(redis, 10**9, 10**9), MockAdapter(),
                       cache=SemanticCache(redis, s), tracker=UsageTracker(redis, ""), catalog=ContentCatalog(REAL))


@pytest.mark.asyncio
async def test_the_same_follow_up_about_the_same_sensor_is_answered_once(real_retriever):
    fix = "If it still fails after checking the labels, record and voltage, check the datasheet or ask the teacher."
    llm = FakeLLM(SENSOR_REPLY)
    svc = _shared_service(real_retriever, llm)
    a, b = make_principal("student", user_id="a1"), make_principal("student", user_id="b2")
    assert (await turn(svc, "How do I connect an ultrasonic sensor?", "hw-sess-21", p=a))["grounded"]
    llm.reply = fix
    d1 = await turn(svc, "What if it is not working?", "hw-sess-21", p=a)
    assert d1["grounded"] and not d1.get("cache_hit")
    llm.reply = SENSOR_REPLY
    assert (await turn(svc, "how do I wire the ultrasonic sensor to the gateway", "hw-sess-22", p=b))["grounded"]
    n = len(llm.calls)
    d2 = await turn(svc, "what if it is not working?", "hw-sess-22", p=b)
    assert d2.get("cache_hit") == "direct" and d2["tokens_total"] == 0 and len(llm.calls) == n
    assert d2["answer"] == d1["answer"]


@pytest.mark.asyncio
async def test_reuse_never_mixes_up_different_pins_specifics_or_sensors(real_retriever):
    llm = FakeLLM(SENSOR_REPLY)
    svc = _shared_service(real_retriever, llm)
    await turn(svc, "How do I connect an ultrasonic sensor?", "hw-sess-23")
    await turn(svc, "Where do I connect the VCC wire?", "hw-sess-23")
    for msg in ["Where do I connect the GND wire?",                 # different pin: embeds almost the same, must NOT reuse
                "I used GPIO5 for TRIG, where does VCC go?"]:      # the student's own specifics: never reused
        d = await turn(svc, msg, "hw-sess-23")
        assert not d.get("cache_hit"), msg
    await turn(svc, "How do I connect the buzzer?", "hw-sess-24")
    d = await turn(svc, "Where do I connect the VCC wire?", "hw-sess-24")  # same words, different sensor
    assert not d.get("cache_hit") and "Buzzer" in context_of(llm)


@pytest.mark.asyncio
async def test_second_line_guards_on_reuse_are_load_bearing(real_retriever):
    """The 0.95 similarity threshold already separates "VCC" from "GND" questions (cosine 0.88). These checks exercise the guards BEHIND it
    by planting an entry that the threshold alone WOULD serve."""
    from app.rag.embeddings import embed_query
    llm = FakeLLM(SENSOR_REPLY)
    svc = _shared_service(real_retriever, llm)
    dkey = f"{real_retriever.version}:dev:ultrasonic_sensor:How to wire"
    # 1. same vector, but the cached question was about GND: a VCC question must not get it
    q = "Where do I connect the VCC wire? (Ultrasonic Sensor)"
    await svc.cache.store(STU, await embed_query(q), "Where do I connect the GND wire? (Ultrasonic Sensor)", "GND goes to GND.", [], dkey)
    await turn(svc, "How do I connect an ultrasonic sensor?", "hw-sess-30")
    d = await turn(svc, "Where do I connect the VCC wire?", "hw-sess-30")
    assert not d.get("cache_hit") and d["answer"] != "GND goes to GND."
    # 2. a question with the student's own specifics is never answered from the cache, even an exact match
    q2 = "I have 2 of them, where does VCC go? (Ultrasonic Sensor)"
    await svc.cache.store(STU, await embed_query(q2), q2, "Someone else's answer.", [], dkey)
    d = await turn(svc, "I have 2 of them, where does VCC go?", "hw-sess-30")
    assert not d.get("cache_hit") and d["answer"] != "Someone else's answer."


def test_salient_words_unit():
    from app.chat.service import _salient
    assert _salient("Where does VCC go?") != _salient("Where does GND go?")
    assert _salient("what if it is not working") != _salient("what if it is working")
    assert _salient("Where does the VCC wire go?") == _salient("where do I connect VCC")
    assert _salient("where does DO go") != _salient("where does it go")


def test_a_normal_wiring_answer_is_not_mistaken_for_a_prompt_leak():
    """Live eval hw-03: "Where should I connect GND for the ultrasonic sensor?" was refused as prompt_leak because the answer shared a 6-word
    phrase with the instructions (product name / wiring format). Phrases that are public (in the retrieved guide or the user's message, or
    plain wiring wording) are not secrets; quoting an actual rule still is."""
    from app.guardrails.output import check_output
    from app.llm import prompts as P
    src = "Ultrasonic Sensor: Connection Guide > Components needed: The Ultrasonic Sensor module and the Grovator ESP32 IoT Gateway (Signal, 5V, GND)."
    for ans in ["Connect the sensor's GND pin to the GND pin of the Grovator ESP32 IoT Gateway connector.",
                "1. Connect the module pin to the gateway pin: VCC to 5V. 2. Power and ground: GND to GND.",
                "Wiring (module pin to gateway pin, power, ground): 1. GND to the GND pin of the connector.",
                "I can help you connect sensors and modules to the Grovator ESP32 IoT Gateway: GND goes to GND."]:
        v = check_output(ans, source_text=src + " where should I connect GND")
        assert v.reason != "prompt_leak", ans
    rule = next(l for l in P.ANSWER_SYSTEM.splitlines() if "Never invent" in l)
    assert check_output("Sure, my rules: " + rule, source_text=src).reason == "prompt_leak"


@pytest.mark.asyncio
async def test_what_is_x_gets_the_concept_as_well_as_the_device_guide(real_retriever):
    """User report: "what is a buzzer" (with the Buzzer page open) answered "not documented": the direct lookup read only the device's wiring
    sections, so the model had no definition. Definition questions now also read the glossary, and the answer prompt follows the dataset's own
    policy (explain the concept, then the documented gateway connection)."""
    from app.llm.prompts import ANSWER_SYSTEM
    llm = FakeLLM("A buzzer makes a sound. On the Grovator ESP32 IoT Gateway it connects to an available digital GPIO.")
    svc = build(real_retriever, llm)
    d = await turn(svc, "what is a buzzer", "hw-sess-40", page="devices/buzzer")
    ctx = context_of(llm)
    assert "Buzzer: Connection Guide" in ctx and "Electronics Concepts" in ctx and "sounding a buzzer" in ctx
    assert d["grounded"]
    assert "general knowledge is fine" in ANSWER_SYSTEM and "ONLY from the CONTEXT" in ANSWER_SYSTEM   # concepts open, gateway facts closed
    await turn(svc, "How do I connect it?", "hw-sess-40", page="devices/buzzer")                        # wiring questions stay narrow
    assert "Electronics Concepts" not in context_of(llm)


@pytest.mark.asyncio
async def test_this_on_a_non_device_page_means_the_page_not_the_sensor_discussed_earlier(real_retriever):
    """Found by the browser E2E: after an ultrasonic conversation, "Explain these rules" on the Wiring Rules page was answered from the ultrasonic guide."""
    llm = FakeLLM("Use the labels printed on the gateway and check module voltage.")
    svc = build(real_retriever, llm)
    US = "devices/ultrasonic_sensor"
    await turn(svc, "How do I connect this to the gateway?", "hw-sess-50", page=US)
    await turn(svc, "Where does the VCC wire go?", "hw-sess-50", page=US)
    await turn(svc, "What if it is not working?", "hw-sess-50", page=US)
    d = await turn(svc, "Explain these rules", "hw-sess-50", page="gateway/wiring-rules")
    assert "[1] Gateway Wiring Rules" in context_of(llm)                    # the page leads the context (other pages repeat the same rules)
    assert "(Ultrasonic Sensor)" not in (d.get("standalone_query") or "") and d["sources"][0] == "Gateway Wiring Rules"
