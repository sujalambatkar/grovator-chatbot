"""Phase 2: short-term memory, router rewriting, budgets, streaming. LLM is scripted; Redis is fakeredis."""
import json

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.chat.service import ChatService
from app.config import get_settings
from app.llm.prompts import MY_DATA_REPLY, OFF_TOPIC_REPLY, ROUTER_SYSTEM, UNSAFE_REPLY
from app.main import create_app
from app.memory.session import SessionStore
from app.rag.tokens import count_tokens
from tests.conftest import FakeLLM, make_principal, make_token


def make_service(retriever, llm):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    s = get_settings()
    return ChatService(llm, retriever, SessionStore(redis, s.session_ttl_s), s), redis


async def turn(svc, p, msg, sid):
    done = None
    async for ev in svc.events(p, msg, sid):
        if ev["type"] == "done":
            done = ev
    await svc.drain()
    return done


def scripted(rewrites):
    """Router that returns the next scripted standalone query for each platform_faq turn."""
    it = iter(rewrites)
    return lambda user: {"intent": "platform_faq", "query": next(it)}


# 5 multi-turn conversations: (user message, router's standalone rewrite, expected source title)
CONVERSATIONS = {
    "robot_debug": [
        ("What parts are in the robotics kit?", "What parts are in the Robotics Foundation kit?", "Robotics Foundation Kit Guide"),
        ("it doesn't move, why?", "Why is my Robotics Foundation robot not moving?", "Robotics Foundation Kit Guide"),
        ("and how do I make it follow a line?", "How do I make the Robotics Foundation robot follow a black line?", ("Robotics Foundation Kit Guide", "Robotics Lesson 4: Line Following")),
    ],
    "iot_to_blocks": [
        ("How do I connect the IoT kit?", "How do I connect the IoT kit to the computer?", "IoT Kit Guide"),
        ("how do I read the temperature one?", "How do I read the IoT kit temperature sensor value?", ("IoT Kit Guide", "IoT Lesson 2: Temperature Sensor")),
        ("now how do I repeat that 5 times?", "How do I repeat blocks 5 times in block coding?", "Loops and Conditions in Block Coding"),
    ],
    "blocks_vars": [
        ("What is block coding?", "What is block coding?", "Block Coding Basics"),
        ("what are variables there?", "What are variables in block coding?", "Loops and Conditions in Block Coding"),
    ],
    "projects": [
        ("Where are my saved programs?", "Where are my saved projects in Govator?", "Projects, Progress and Getting Help"),
        ("can I get deleted ones back?", "Can I recover a deleted project in Govator?", "Projects, Progress and Getting Help"),
    ],
    "login": [
        ("How do I log in?", "How do I log in to Govator?", "Getting Started on Govator"),
        ("I forgot it", "What do I do if I forgot my Govator password?", "Getting Started on Govator"),
    ],
}


@pytest.mark.parametrize("name", CONVERSATIONS)
async def test_multi_turn_followups_resolved_by_rewrite(retriever, name):
    conv = CONVERSATIONS[name]
    llm = FakeLLM("Ok.", router_fn=scripted([c[1] for c in conv]))
    svc, _ = make_service(retriever, llm)
    p, sid = make_principal("student"), "sess-" + name
    for i, (msg, rewrite, title) in enumerate(conv):
        done = await turn(svc, p, msg, sid)
        assert done["grounded"], (msg, done)
        assert done["standalone_query"] == rewrite
        assert any(x in done["sources"] for x in ((title,) if isinstance(title, str) else title)), (msg, done["sources"])
        if i > 0:  # router received the rolling summary built from earlier turns
            assert "SUMMARY: Summary:" in llm.router_calls[i]


async def test_only_last_two_turns_and_incremental_summary(retriever):
    llm = FakeLLM("Ok.")
    svc, redis = make_service(retriever, llm)
    p = make_principal("student")
    qs = ["How do I log in?", "What is block coding?", "What sensors are in the IoT kit?",
          "How do I make the robot follow a line?", "Where are my saved projects?"]
    for q in qs:
        await turn(svc, p, q, "sess-window")
    state = await svc.sessions.get(p.user_id, "sess-window")
    assert [t.user for t in state.turns] == qs[-2:]
    last_prompt = llm.calls[-1]
    assert len(last_prompt) == 1 + 2 * 2 + 1  # system + 2 prior exchanges + current
    assert "log in" not in json.dumps(last_prompt).lower().replace("login", "")  # oldest turn gone
    # summary updater sees old summary + latest turn only, never earlier history
    assert "OLD SUMMARY: Summary: " in llm.summary_calls[-1]  # carries the previous summary forward
    assert "How do I log in?" not in llm.summary_calls[-1]
    assert count_tokens(state.summary) <= get_settings().summary_max_tokens


async def test_session_isolated_per_user_and_has_ttl(retriever):
    llm = FakeLLM("Ok.")
    svc, redis = make_service(retriever, llm)
    a, b = make_principal("student", user_id="alice"), make_principal("student", user_id="bob")
    await turn(svc, a, "How do I log in?", "shared-sid-1")
    await turn(svc, b, "What is block coding?", "shared-sid-1")  # same session_id, different user
    assert "SUMMARY: (none)" in llm.router_calls[1]
    assert "log in" not in llm.router_calls[1].lower()
    assert 0 < await redis.ttl("govator:sess:alice:shared-sid-1") <= get_settings().session_ttl_s
    keys = {k async for k in redis.scan_iter("govator:sess:*")}
    assert keys == {"govator:sess:alice:shared-sid-1", "govator:sess:bob:shared-sid-1"}


async def test_answer_prompt_respects_input_budget_with_huge_history(retriever):
    llm = FakeLLM("Ok.")
    svc, _ = make_service(retriever, llm)
    p = make_principal("student")
    long = "robot " * 400
    for _ in range(3):
        await turn(svc, p, "How do I connect the IoT kit? " + long[:900], "sess-big")
    done = await turn(svc, p, "How do I connect the IoT kit?", "sess-big")
    assert done["input_tokens"] <= get_settings().answer_total_budget


@pytest.mark.parametrize("intent,expected", [("unsafe", UNSAFE_REPLY), ("off_topic", OFF_TOPIC_REPLY), ("my_data", MY_DATA_REPLY)])
async def test_canned_intents_skip_answer_model_and_memory(retriever, intent, expected):
    llm = FakeLLM(router_fn=lambda u: {"intent": intent, "query": "x"})
    svc, redis = make_service(retriever, llm)
    done = await turn(svc, make_principal("student"), "whatever", "sess-canned")
    assert done["answer"] == expected and not done["grounded"] and llm.calls == []
    assert await redis.keys("govator:sess:*") == []  # refusals leave no memory


async def test_smalltalk_uses_router_reply_no_retrieval(retriever):
    llm = FakeLLM(router_fn=lambda u: {"intent": "smalltalk", "query": "hi", "reply": "Hi there! Ready to code?"})
    svc, _ = make_service(retriever, llm)
    done = await turn(svc, make_principal("student"), "hello", "sess-hi")
    # the router's own wording is NOT shown (a model must never free-write small talk): the fixed redirect is
    from app.llm.prompts import REDIRECT_REPLY
    assert done["answer"] == REDIRECT_REPLY and llm.calls == []


async def test_router_failure_falls_back_to_retrieval(retriever):
    llm = FakeLLM("Ok.")
    from app.llm.llm_client import LLMError
    llm.router_error = LLMError("down")
    svc, _ = make_service(retriever, llm)
    done = await turn(svc, make_principal("student"), "How do I connect the IoT kit?", "sess-fb")
    assert done["intent"] == "platform_faq" and done["grounded"]
    # a follow-up with the router down is still searchable: previous question is prepended
    llm.router_error = LLMError("down")
    done = await turn(svc, make_principal("student"), "and the light one?", "sess-fb")
    assert done["standalone_query"] == "How do I connect the IoT kit? and the light one?"


async def test_router_bad_json_and_unknown_intent_fall_back(retriever):
    llm = FakeLLM("Ok.", router_fn=lambda u: {"intent": "hack_the_planet", "query": "x"})
    svc, _ = make_service(retriever, llm)
    done = await turn(svc, make_principal("student"), "How do I connect the IoT kit?", "sess-bad")
    assert done["intent"] == "platform_faq" and done["standalone_query"] == "How do I connect the IoT kit?"


def test_router_prompt_within_budget():
    assert count_tokens(ROUTER_SYSTEM) <= 150


# ---- HTTP / SSE ----

def parse_sse(text: str):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def client(retriever):
    llm = FakeLLM("Press Connect Kit now.")
    with TestClient(create_app(llm=llm, retriever=retriever)) as c:
        c.llm = llm
        yield c


def auth(role="student"):
    return {"Authorization": f"Bearer {make_token(role)}"}


def test_sse_stream_event_order(client):
    r = client.post("/chat/stream", json={"message": "How do I connect the IoT kit?", "session_id": "sse-session-1"}, headers=auth())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "meta" and kinds[-1] == "done" and kinds.count("token") >= 2
    assert "".join(d["t"] for k, d in events if k == "token").strip() == "Press Connect Kit now."
    assert events[-1][1]["session_id"] == "sse-session-1" and events[-1][1]["grounded"]


def test_chat_returns_session_id_and_validates(client):
    r = client.post("/chat", json={"message": "How do I connect the IoT kit?"}, headers=auth())
    assert r.status_code == 200 and len(r.json()["session_id"]) >= 8
    assert client.post("/chat", json={"message": "hi", "session_id": "../etc"}, headers=auth()).status_code == 422
    assert client.post("/chat", json={"message": "   "}, headers=auth()).status_code == 422


def test_delete_session(client):
    sid = "delete-me-123"
    client.post("/chat", json={"message": "How do I connect the IoT kit?", "session_id": sid}, headers=auth())
    assert client.delete(f"/chat/sessions/{sid}", headers=auth()).status_code == 204
    assert client.delete("/chat/sessions/x", headers=auth()).status_code == 422
