import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.llm.prompts import ANSWER_SYSTEM, NO_INFO_REPLY
from app.main import create_app
from app.rag.tokens import count_tokens
from tests.conftest import KNOWLEDGE_DIR, FakeLLM, make_principal, make_token


def _hidden_from(role: str) -> set[str]:
    """Sources whose front-matter role_visibility excludes `role` (derived from the real files, not hard-coded)."""
    import frontmatter
    root = KNOWLEDGE_DIR
    return {str(p.relative_to(root)) for p in root.rglob("*.md") if p.name.lower() != "readme.md"
            and role not in frontmatter.load(p).get("role_visibility", ["student", "teacher", "admin", "superadmin"])}


async def test_student_never_retrieves_teacher_or_admin_docs(retriever):
    hidden = _hidden_from("student")
    assert {"teacher/batches-and-reports.md", "admin/school-administration.md", "tools/class-dashboard.md", "tools/report-builder.md"} <= hidden
    for q in ["How do I reset a student's password?", "How do I assign a teacher to a batch?",
              "How do I create a new school?", "Download batch report PDF", "Show me the class dashboard", "How do I build a report?"]:
        res = await retriever.retrieve(q, make_principal("student"), k=8)
        for h in res.hits:
            assert h.source not in hidden, (q, h.source)


async def test_teacher_cannot_see_superadmin_docs(retriever):
    res = await retriever.retrieve("How do I create a new school?", make_principal("teacher"), k=8)
    assert all("org-administration" not in h.source for h in res.hits)


async def test_tenant_scoped_content_is_isolated(retriever):
    from app.rag.chunking import chunk_document
    from app.rag.embeddings import embed_docs
    chunks = chunk_document("# Rules\nSchool X lab opens at 7am on Mondays.", title="School X Rules",
                            source="x.md", role_visibility=["student"], org_id="orgX", school_id="schX")
    await retriever.store.upsert(chunks, await embed_docs([c.text for c in chunks]))
    retriever.invalidate()
    other = await retriever.retrieve("When does the School X lab open?", make_principal("student"), k=8)
    assert all(h.source != "x.md" for h in other.hits)
    own = await retriever.retrieve("When does the School X lab open?",
                                   make_principal("student", org_id="orgX", school_id="schX"), k=8)
    assert any(h.source == "x.md" for h in own.hits)


@pytest.fixture
def client(retriever):
    llm = FakeLLM("Press Connect Kit.")
    app = create_app(llm=llm, retriever=retriever)
    with TestClient(app) as c:
        c.llm = llm
        yield c


def auth(role="student"):
    return {"Authorization": f"Bearer {make_token(role)}"}


def test_chat_requires_auth(client):
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/chat", json={"message": "hi"}, headers={"Authorization": "Bearer junk"}).status_code == 401


def test_chat_grounded_answer_and_token_budget(client):
    r = client.post("/chat", json={"message": "How do I connect the IoT kit?"}, headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert body["grounded"] and body["answer"] == "Press Connect Kit." and "IoT Kit Guide" in body["sources"]
    assert body["input_tokens"] <= 1200
    # no user identifiers are sent to the model
    sent = " ".join(m["content"] for m in client.llm.calls[-1])
    assert "u-student" not in sent and "org1" not in sent


def test_chat_off_topic_skips_llm(client):
    n = len(client.llm.calls)
    r = client.post("/chat", json={"message": "What is the capital of France?"}, headers=auth())
    assert r.json()["answer"] == NO_INFO_REPLY and not r.json()["grounded"]
    assert len(client.llm.calls) == n


def test_message_validation(client):
    assert client.post("/chat", json={"message": ""}, headers=auth()).status_code == 422
    assert client.post("/chat", json={"message": "x" * 1001}, headers=auth()).status_code == 422


def test_system_prompt_within_budget():
    assert count_tokens(ANSWER_SYSTEM) <= 200
