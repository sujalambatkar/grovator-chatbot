"""Demo features: shared content catalog, page-aware answers, per-answer token accounting, demo login."""
from pathlib import Path

import fakeredis.aioredis
import jwt
import pytest
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from app.chat.cache import SemanticCache
from app.chat.service import ChatService
from app.config import Settings
from app.content.catalog import ContentCatalog
from app.core.abuse import TokenBudget
from app.core.usage import UsageTracker
from app.main import create_app
from app.memory.long_term import MemoryManager, MemoryStore
from app.memory.session import SessionStore
from app.tools.mock_adapter import MockAdapter
from tests.conftest import FakeLLM, make_principal, make_token

ON = dict(enable_semantic_cache=True, enable_router_shortcut=True, lazy_summary=True, enable_compression=True, usage_log_dir="")
TEMP_PAGE = "lessons/iot-2-temperature-sensor.md"
LINE_PAGE = "lessons/rob-4-line-following.md"
TEMP_REPLY = ("Plug the temperature sensor into port 1 and check the port number in your blocks matches. "
              "Add display text and drop the temperature value block into it, then press Run.")
STU, TEA = make_principal("student", user_id="s1"), make_principal("teacher", user_id="t1")
CATALOG = ContentCatalog(Path(Settings().knowledge_dir))


def build(retriever, llm, **over):
    s = Settings(**{**ON, **over})
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    mm = MemoryManager(MemoryStore(AsyncQdrantClient(location=":memory:"), "m"), llm, s)
    return ChatService(llm, retriever, SessionStore(redis, 600), s, mm, TokenBudget(redis, 10**9, 10**9), MockAdapter(),
                       cache=SemanticCache(redis, s), tracker=UsageTracker(redis, ""), catalog=CATALOG)


async def turn(svc, p, msg, sid, page=None):
    events = [e async for e in svc.events(p, msg, sid, page)]
    await svc.drain()
    return [e for e in events if e["type"] == "done"][0]


# ------------------------------------------------------------------ catalog
def test_catalog_is_complete_and_ordered():
    docs = CATALOG.visible(STU)
    cats = [d.category for d in docs]
    assert {"courses", "lessons", "blocks", "tools", "help"} <= set(cats)
    assert cats == sorted(cats, key=lambda c: ["courses", "lessons", "blocks", "tools", "help"].index(c))
    assert sum(c == "lessons" for c in cats) >= 8 and sum(c == "blocks" for c in cats) >= 7
    assert all(d.body and d.tokens > 0 for d in docs)


def test_catalog_visibility_matches_role_and_hides_without_revealing():
    stu = {d.source for d in CATALOG.visible(STU)}
    tea = {d.source for d in CATALOG.visible(TEA)}
    assert "tools/class-dashboard.md" not in stu and "tools/class-dashboard.md" in tea
    assert "admin/school-administration.md" not in tea
    assert CATALOG.get(STU, "tools/class-dashboard.md") is None            # hidden
    assert CATALOG.get(STU, "no/such/page.md") is None                     # unknown: identical to hidden
    assert CATALOG.get(STU, TEMP_PAGE).title == "IoT Lesson 2: Temperature Sensor"
    assert CATALOG.total_tokens(STU) < CATALOG.total_tokens(TEA) and CATALOG.total_tokens(STU) > 5000


@pytest.fixture
def client(retriever):
    with TestClient(create_app(llm=FakeLLM(TEMP_REPLY), retriever=retriever,
                               settings=Settings(**ON, rate_limit_user_per_min=1000, rate_limit_ip_per_min=1000))) as c:
        yield c


def auth(role="student", user="s1"):
    return {"Authorization": f"Bearer {make_token(role, sub=user)}"}


def test_content_endpoint_serves_what_the_user_may_read(client):
    assert client.get("/content").status_code == 401
    stu = client.get("/content", headers=auth()).json()
    assert {"courses", "lessons", "blocks", "tools"} <= set(stu["categories"])
    sources = {d["source"] for d in stu["docs"]}
    assert TEMP_PAGE in sources and "tools/class-dashboard.md" not in sources
    assert all(d["body"] for d in stu["docs"])
    tea = {d["source"] for d in client.get("/content", headers=auth("teacher", "t1")).json()["docs"]}
    assert "tools/class-dashboard.md" in tea


# ------------------------------------------------------------------ page-aware answers
async def test_page_context_makes_vague_questions_answerable(retriever):
    llm = FakeLLM(TEMP_REPLY)
    svc = build(retriever, llm, enable_semantic_cache=False)
    without = await turn(svc, STU, "explain this lesson step by step", "sess-pg-0001")
    assert not without["grounded"]                                                       # no page: nothing to anchor "this"
    with_page = await turn(svc, STU, "explain this lesson step by step", "sess-pg-0002", TEMP_PAGE)
    assert with_page["grounded"] and "IoT Lesson 2: Temperature Sensor" in with_page["sources"]
    assert "Temperature Sensor" in llm.calls[-1][-1]["content"]                          # the lesson text reached the model


@pytest.mark.parametrize("q", ["what do I do in step 3?", "Explain these blocks with an example", "what are the common mistakes here?"])
async def test_questions_that_point_at_the_page_lead_with_the_pages_own_content(retriever, q):
    llm = FakeLLM("Check that the robot sees the line, then move forward, else turn right 10 degrees.")
    svc = build(retriever, llm, enable_semantic_cache=False)
    await turn(svc, STU, q, "sess-lead-0001", LINE_PAGE)
    assert "CONTEXT:\n[1] Robotics Lesson 4: Line Following" in llm.calls[-1][-1]["content"], q


async def test_ties_go_to_the_page_being_read_but_clearly_different_topics_do_not(retriever):
    reply = "Plug the temperature sensor into port 1 and press Run."
    llm = FakeLLM(reply)
    svc = build(retriever, llm, enable_semantic_cache=False)
    # every lesson has a 'Common mistakes' section: the one on the open page should lead
    await turn(svc, STU, "What are the common mistakes?", "sess-tie-0001", TEMP_PAGE)
    assert "CONTEXT:\n[1] IoT Lesson 2: Temperature Sensor" in llm.calls[-1][-1]["content"]
    await turn(svc, STU, "What are the common mistakes?", "sess-tie-0002", LINE_PAGE)
    assert "CONTEXT:\n[1] Robotics Lesson 4: Line Following" in llm.calls[-1][-1]["content"]
    # a question about something else entirely is NOT hijacked by the open page
    await turn(svc, STU, "How do I log in to Govator?", "sess-tie-0003", TEMP_PAGE)
    assert "CONTEXT:\n[1] Getting Started on Govator" in llm.calls[-1][-1]["content"]


async def test_hidden_or_unknown_page_is_ignored_not_trusted(retriever):
    llm = FakeLLM(TEMP_REPLY)
    svc = build(retriever, llm, enable_semantic_cache=False)
    for page in ("tools/class-dashboard.md", "no/such/page.md"):                          # teacher-only / nonexistent
        d = await turn(svc, STU, "explain this page", f"sess-hid-{abs(hash(page)) % 10**6}", page)
        assert "Class Dashboard" not in " ".join(d["sources"])
        assert all("Class Dashboard" not in m["content"] for call in llm.calls for m in call)  # never reached the model
        assert d["naive_tokens"] > 5000


async def test_answers_are_cached_per_page_not_shared_across_pages(retriever):
    llm = FakeLLM(TEMP_REPLY)
    svc = build(retriever, llm)
    q = "What will I do in this lesson?"
    a = await turn(svc, STU, q, "sess-cp-0001", TEMP_PAGE)
    same_page = await turn(svc, make_principal("student", user_id="s2"), q, "sess-cp-0002", TEMP_PAGE)
    other_page = await turn(svc, make_principal("student", user_id="s3"), q, "sess-cp-0003", LINE_PAGE)
    assert a["grounded"] and same_page.get("cache_hit") == "pre_router"
    assert not other_page.get("cache_hit")                                               # same words, different page


@pytest.mark.parametrize("page", ["../etc/passwd x", "a b", "x" * 121, "<script>"])
def test_page_parameter_is_validated(client, page):
    r = client.post("/chat", json={"message": "hello there", "page": page}, headers=auth())
    assert r.status_code == 422


# ------------------------------------------------------------------ token accounting
async def test_each_answer_reports_its_tokens_and_the_naive_estimate(retriever):
    llm = FakeLLM(TEMP_REPLY)
    svc = build(retriever, llm, enable_semantic_cache=False)
    d1 = await turn(svc, STU, "How do I read the temperature sensor?", "sess-tk-0001", TEMP_PAGE)
    assert d1["tokens_total"] == d1["router_tokens"] + d1["input_tokens"] + d1["output_tokens"] > 0
    assert d1["naive_tokens"] > 5000 > d1["tokens_total"] * 5      # whole site vs the few chunks RAG sends
    d2 = await turn(svc, STU, "and how do I show it on the display?", "sess-tk-0001", TEMP_PAGE)
    assert d2["naive_tokens"] > d1["naive_tokens"]                  # a naive bot also re-sends the growing chat every turn
    assert d2["tokens_total"] < 1300                                # ours stays flat: summary + last 2 turns + a few chunks


async def test_cache_hit_costs_zero_tokens_but_still_reports_the_naive_cost(retriever):
    svc = build(retriever, FakeLLM(TEMP_REPLY))
    await turn(svc, STU, "How do I read the temperature sensor?", "sess-ch-0001", TEMP_PAGE)
    hit = await turn(svc, make_principal("student", user_id="s2"), "How do I read the temperature sensor?", "sess-ch-0002", TEMP_PAGE)
    assert hit["cache_hit"] and hit["tokens_total"] == 0 and hit["naive_tokens"] > 5000


def test_chat_response_exposes_token_fields(client):
    r = client.post("/chat", json={"message": "How do I read the temperature sensor?", "page": TEMP_PAGE}, headers=auth()).json()
    assert r["tokens_total"] > 0 and r["naive_tokens"] > r["tokens_total"]


# ------------------------------------------------------------------ demo login
def test_demo_login_is_off_by_default(retriever):
    with TestClient(create_app(llm=FakeLLM(), retriever=retriever, settings=Settings(**ON))) as c:
        assert c.post("/demo/session", json={"role": "student"}).status_code == 404


@pytest.mark.parametrize("role,user", [("student", "s1"), ("teacher", "t1")])
def test_demo_login_issues_a_working_token(retriever, role, user):
    s = Settings(**ON, demo_mode=True)
    with TestClient(create_app(llm=FakeLLM(TEMP_REPLY), retriever=retriever, settings=s)) as c:
        body = c.post("/demo/session", json={"role": role}).json()
        claims = jwt.decode(body["token"], s.jwt_secret, algorithms=[s.jwt_algorithm])
        assert claims["role"] == role and claims["sub"] == user and claims["exp"] > claims["exp"] - 1
        r = c.post("/chat", json={"message": "How do I read the temperature sensor?"}, headers={"Authorization": f"Bearer {body['token']}"})
        assert r.status_code == 200
        assert c.post("/demo/session", json={"role": "superadmin"}).status_code == 422   # only the two demo roles


def test_demo_mode_cannot_ship_to_production(retriever):
    from app.config import production_problems
    prod = dict(environment="production", jwt_secret="x" * 40, groq_api_key="k", platform_adapter="none",
                redis_url="redis://r", qdrant_url="http://q", cors_origins="https://app.example")
    assert production_problems(Settings(**prod)) == []
    assert any("DEMO_MODE" in p for p in production_problems(Settings(**prod, demo_mode=True)))
    with pytest.raises(RuntimeError, match="DEMO_MODE"):
        create_app(llm=FakeLLM(), retriever=retriever, settings=Settings(**prod, demo_mode=True))


# ------------------------------------------------------------------ "this platform" is not "this page"
@pytest.mark.parametrize("q", ["what exactly is this platform", "what is this website?", "who made this app", "what can I do on this site"])
async def test_this_platform_is_not_a_pointer_to_the_open_page(retriever, q):
    """Regression (reported from the UI): 'what exactly is this platform' on a lesson page was rewritten as
    '<lesson title>: ...' and the model was given only that lesson, so it (rightly) had no answer."""
    llm = FakeLLM("Govator is a learning platform for schools: a website and app where students learn coding, electronics and robotics. "
                  "You can follow courses, open lessons, build programs in the Block Workspace, test them in the Simulator and track your progress. "
                  "It is made by Grok Learning.")
    svc = build(retriever, llm, enable_semantic_cache=False)
    d = await turn(svc, STU, q, "sess-plat-0001", TEMP_PAGE)
    prompt = llm.calls[-1][-1]["content"]
    assert "About Govator" in prompt, (q, prompt[:700])
    assert not d["standalone_query"].startswith("IoT Lesson 2"), d["standalone_query"]   # the query was not hijacked
    assert "[1] About Govator" in prompt                                                 # and the overview leads the context


@pytest.mark.parametrize("q", ["Explain this lesson step by step", "what do I do in this step", "explain these blocks", "what is this program doing"])
def test_real_pointers_to_the_page_still_count(q):
    from app.chat.shortcut import DEICTIC
    assert DEICTIC.search(q)


@pytest.mark.parametrize("q", ["what exactly is this platform", "who made this app", "what is this website", "is this system safe"])
def test_platform_words_after_this_are_not_pointers(q):
    from app.chat.shortcut import DEICTIC
    assert not DEICTIC.search(q)


# ------------------------------------------------------------------ the bot must know the SEQUENCE of lessons
import re as _re


def _lessons_by_course():
    lessons = [d for d in CATALOG.docs if d.category == "lessons"]
    return {"IoT": sorted((d for d in lessons if d.source.startswith("lessons/iot-")), key=lambda d: d.order),
            "Robotics": sorted((d for d in lessons if d.source.startswith("lessons/rob-")), key=lambda d: d.order)}


def test_every_lesson_page_states_its_previous_and_next_lesson_and_they_match_the_real_order():
    """The page text and the catalog order are two descriptions of one sequence: they must never disagree."""
    for course, docs in _lessons_by_course().items():
        assert len(docs) == 4
        for i, d in enumerate(docs):
            prev_m = _re.search(r"Previous lesson: (.+?)\.\s*(?:\n|$)", d.body)
            next_m = _re.search(r"Next lesson: (.+?)\.\s*(?:\n|$)", d.body)
            assert prev_m and next_m, f"{d.source} has no 'Previous lesson / Next lesson' lines"
            want_prev = docs[i - 1].title if i > 0 else "none"
            want_next = docs[i + 1].title if i < len(docs) - 1 else "none"
            assert prev_m.group(1).startswith(want_prev), (d.source, prev_m.group(1), want_prev)
            assert next_m.group(1).startswith(want_next), (d.source, next_m.group(1), want_next)


def test_a_learning_path_page_lists_both_courses_in_order():
    path = next(d for d in CATALOG.docs if d.source == "courses/learning-path.md")
    for course, docs in _lessons_by_course().items():
        positions = [path.body.find(d.title) for d in docs]
        assert all(p >= 0 for p in positions), (course, positions)
        assert positions == sorted(positions), f"{course} lessons are not listed in order"


@pytest.mark.parametrize("q,page,must_have", [
    ("what is the next lesson after led and buzzer", TEMP_PAGE, ["Send Data to the Dashboard"]),
    ("what is the next lesson after led and buzzer", None, ["Send Data to the Dashboard"]),
    ("what is the next lesson after led and buzzer", "lessons/iot-3-led-and-buzzer.md", ["Send Data to the Dashboard"]),
    ("what comes next?", "lessons/iot-3-led-and-buzzer.md", ["Send Data to the Dashboard"]),
    ("what should I do next", "lessons/rob-2-move-and-turn.md", ["Distance Sensor"]),
    ("which lesson comes before line following", None, ["Distance Sensor"]),
    ("what is the first lesson in robotics foundation", None, ["Meet Your Robot"]),
    ("what is the last lesson in IoT foundations", None, ["Send Data to the Dashboard"]),
    ("what comes after move and turn", None, ["Distance Sensor"]),
    ("in what order should I do the iot lessons", None, ["What is IoT", "Temperature Sensor", "LED and Buzzer"]),
])
async def test_sequence_questions_reach_the_model_with_the_answer_in_context(retriever, q, page, must_have):
    llm = FakeLLM("The next lesson is IoT Lesson 4: Send Data to the Dashboard.")
    svc = build(retriever, llm, enable_semantic_cache=False)
    await turn(svc, STU, q, "sess-seq-0001", page)
    prompt = llm.calls[-1][-1]["content"]
    assert all(m in prompt for m in must_have), (q, page, [m for m in must_have if m not in prompt], prompt[:900])


@pytest.mark.parametrize("q", ["what is the first step to connect the IoT kit?", "how do I stop the robot before it hits a wall?",
                               "what is the last block in the temperature program?"])
async def test_ordinary_questions_do_not_pull_in_the_learning_path(retriever, q):
    llm = FakeLLM(TEMP_REPLY)
    svc = build(retriever, llm, enable_semantic_cache=False)
    await turn(svc, STU, q, "sess-noseq-0001")
    assert "Learning Path" not in llm.calls[-1][-1]["content"], q


def test_sequence_question_detector():
    from app.chat.shortcut import SEQUENCE_Q
    for q in ["what is the next lesson after led and buzzer", "what comes after move and turn", "which lesson comes before line following",
              "what is the first lesson in robotics foundation", "in what order should I do the iot lessons", "what is the last lesson in IoT"]:
        assert SEQUENCE_Q.search(q), q
    for q in ["what is the first step to connect the IoT kit?", "how do I stop the robot", "what is the last block in the program",
              "what is a loop", "which block turns the LED on"]:
        assert not SEQUENCE_Q.search(q), q


@pytest.mark.parametrize("q,needs", [
    ("in what order should I do the robotics lessons", ["Meet Your Robot", "Move and Turn", "Distance Sensor", "Line Following", "Obstacle Course", "Maze Runner"]),
    ("in what order should I do the IoT lessons", ["What is IoT", "Temperature Sensor", "LED and Buzzer", "Send Data to the Dashboard", "Smart Room Alert"]),
])
async def test_order_questions_get_the_whole_list_not_a_compressed_excerpt(retriever, q, needs):
    """Regression (live): 'in what order...' was answered with three lessons and '(and so on...)' because sentence-level
    compression kept only the best sentences of the list. A list the user asked for in full must not be compressed."""
    llm = FakeLLM("Do the lessons in the order listed in the learning path.")
    svc = build(retriever, llm, enable_semantic_cache=False)
    await turn(svc, STU, q, "sess-list-0001")
    prompt = llm.calls[-1][-1]["content"]
    assert all(n in prompt for n in needs), (q, [n for n in needs if n not in prompt])
