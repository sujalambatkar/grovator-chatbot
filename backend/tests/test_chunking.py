from app.rag.chunking import MAX_TOKENS, chunk_document
from app.rag.tokens import count_tokens


def test_chunks_respect_token_limit_and_overlap():
    body = "# Topic\n" + " ".join(f"Sentence number {i} is about robots and sensors." for i in range(200))
    chunks = chunk_document(body, title="T", source="t.md", role_visibility=["student"])
    assert len(chunks) > 2
    for c in chunks:
        assert count_tokens(c.text) <= MAX_TOKENS + 20  # heading prefix allowance
        assert c.summary and len(c.summary) <= 160
    # overlap: the end of one chunk reappears at the start of the next
    tail = chunks[0].text.split(". ")[-2]
    assert tail in chunks[1].text


def test_heading_context_is_in_chunk():
    chunks = chunk_document("# Loops\nUse repeat.", title="Blocks", source="b.md", role_visibility=["student"])
    assert chunks[0].text.startswith("Blocks > Loops:")
