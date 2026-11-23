"""Phase 6 backend: request ids, feedback, CORS."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeLLM, make_token

GOOD = "Connect the controller board to your computer with the USB cable. Open the IoT subject in Grovator and press Connect Kit."
Q = "How do I connect the IoT kit?"
ON = dict(enable_semantic_cache=False, enable_router_shortcut=True, lazy_summary=True, enable_compression=True, usage_log_dir="",
          rate_limit_user_per_min=1000, rate_limit_ip_per_min=1000)


def auth(role="student", user="alice", **kw):
    return {"Authorization": f"Bearer {make_token(role, sub=user, **kw)}"}


@pytest.fixture
def client(retriever):
    with TestClient(create_app(llm=FakeLLM(GOOD), retriever=retriever, settings=Settings(**ON))) as c:
        yield c


def ask(client, user="alice", **kw):
    r = client.post("/chat", json={"message": Q}, headers=auth(user=user, **kw))
    assert r.status_code == 200, r.text
    return r.json()


def test_every_answer_carries_a_request_id_and_cache_flag(client):
    body = ask(client)
    assert re.fullmatch(r"[0-9a-f]{32}", body["request_id"]) and "cache_hit" in body
    assert ask(client)["request_id"] != body["request_id"]


def test_sse_done_event_has_request_id(client):
    r = client.post("/chat/stream", json={"message": Q}, headers=auth())
    done = [b for b in r.text.strip().split("\n\n") if b.startswith("event: done")][0]
    assert re.fullmatch(r"[0-9a-f]{32}", json.loads(done.split("data: ", 1)[1])["request_id"])


def test_feedback_roundtrip_and_aggregates(client):
    rid = ask(client)["request_id"]
    assert client.post("/chat/feedback", json={"request_id": rid, "rating": "up"}, headers=auth()).status_code == 204
    rid2 = ask(client)["request_id"]
    assert client.post("/chat/feedback", json={"request_id": rid2, "rating": "down", "reason": "unclear"}, headers=auth()).status_code == 204
    sm = client.get("/admin/chatbot/usage", headers=auth("admin", "a1")).json()["feedback"]
    assert sm["up"] == 1 and sm["down"] == 1 and sm["satisfaction"] == 0.5 and sm["down_reasons"] == {"unclear": 1}


def test_changing_a_rating_adjusts_counters_instead_of_double_counting(client):
    rid = ask(client)["request_id"]
    for rating in ("up", "up", "down", "up"):
        assert client.post("/chat/feedback", json={"request_id": rid, "rating": rating}, headers=auth()).status_code == 204
    fb = client.get("/admin/chatbot/usage", headers=auth("admin", "a1")).json()["feedback"]
    assert (fb["up"], fb["down"]) == (1, 0)


def fb(client, rid, rating, reason=None, **kw):
    body = {"request_id": rid, "rating": rating, **({"reason": reason} if reason else {})}
    assert client.post("/chat/feedback", json=body, headers=auth(**kw)).status_code == 204
    return client.get("/admin/chatbot/usage", headers=auth("admin", "a1")).json()["feedback"]


def test_reason_sent_after_the_thumbs_down_is_recorded(client):
    """The UI saves 👎 first and the reason chip second: the second call has the same rating but new information."""
    rid = ask(client)["request_id"]
    f = fb(client, rid, "down")
    assert (f["up"], f["down"], f["down_reasons"]) == (0, 1, {})
    f = fb(client, rid, "down", "unclear")
    assert (f["up"], f["down"], f["down_reasons"]) == (0, 1, {"unclear": 1})
    f = fb(client, rid, "down", "unclear")                       # repeating is a no-op
    assert (f["down"], f["down_reasons"]) == (1, {"unclear": 1})


def test_changing_the_reason_moves_the_count_and_switching_to_up_retracts_it(client):
    rid = ask(client)["request_id"]
    fb(client, rid, "down", "wrong")
    f = fb(client, rid, "down", "unsafe")
    assert f["down"] == 1 and {k: v for k, v in f["down_reasons"].items() if v} == {"unsafe": 1}
    f = fb(client, rid, "up")
    assert (f["up"], f["down"]) == (1, 0) and not any(f["down_reasons"].values())  # a reason never outlives its thumbs-down


def test_only_the_owner_can_rate_a_request(client):
    rid = ask(client, user="alice")["request_id"]
    assert client.post("/chat/feedback", json={"request_id": rid, "rating": "down"}, headers=auth(user="bob")).status_code == 404
    assert client.post("/chat/feedback", json={"request_id": rid, "rating": "down"}, headers=auth(user="alice", org_id="org2")).status_code == 404
    fb = client.get("/admin/chatbot/usage", headers=auth("admin", "a1")).json()["feedback"]
    assert (fb["up"], fb["down"]) == (0, 0)  # the attempts changed nothing


@pytest.mark.parametrize("body,code", [
    ({"request_id": "0" * 32, "rating": "up"}, 404),                       # unknown id
    ({"request_id": "not-an-id", "rating": "up"}, 422),
    ({"request_id": "0" * 32, "rating": "meh"}, 422),
    ({"request_id": "0" * 32, "rating": "down", "reason": "free text"}, 422),
    ({"request_id": "0" * 32, "rating": "down", "comment": "my phone is 9876543210"}, 422),  # no free-text channel
])
def test_feedback_validation(client, body, code):
    assert client.post("/chat/feedback", json=body, headers=auth()).status_code == code


def test_feedback_requires_auth(client):
    assert client.post("/chat/feedback", json={"request_id": "0" * 32, "rating": "up"}).status_code == 401


def test_request_id_is_written_to_the_usage_log(retriever, tmp_path):
    s = Settings(**{**ON, "usage_log_dir": str(tmp_path)})
    with TestClient(create_app(llm=FakeLLM(GOOD), retriever=retriever, settings=s)) as c:
        rid = ask(c)["request_id"]
    rec = json.loads(next(tmp_path.glob("usage-*.jsonl")).read_text().splitlines()[0])
    assert rec["request_id"] == rid and "message" not in rec and "answer" not in rec


# ---- CORS ----
def preflight(client, origin):
    return client.options("/chat/stream", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                    "Access-Control-Request-Headers": "authorization,content-type"})


def test_cors_allows_configured_origin_only(client):
    ok = preflight(client, "http://localhost:3000")
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "authorization" in ok.headers["access-control-allow-headers"].lower()
    bad = preflight(client, "https://evil.example")
    assert "access-control-allow-origin" not in bad.headers
    assert "access-control-allow-credentials" not in ok.headers  # bearer tokens, no cookies


def test_cors_wildcard_is_ignored(retriever):
    s = Settings(**ON, cors_origins="*")
    with TestClient(create_app(llm=FakeLLM(GOOD), retriever=retriever, settings=s)) as c:
        assert "access-control-allow-origin" not in preflight(c, "https://evil.example").headers


def test_retry_after_is_exposed_to_browsers(retriever):
    s = Settings(**{**ON, "rate_limit_user_per_min": 1})
    with TestClient(create_app(llm=FakeLLM(GOOD), retriever=retriever, settings=s)) as c:
        c.post("/chat", json={"message": Q}, headers={**auth(), "Origin": "http://localhost:3000"})
        r = c.post("/chat", json={"message": Q}, headers={**auth(), "Origin": "http://localhost:3000"})
    assert r.status_code == 429 and "retry-after" in r.headers
    assert "retry-after" in r.headers["access-control-expose-headers"].lower()
