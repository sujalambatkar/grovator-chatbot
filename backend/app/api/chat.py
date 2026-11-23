import json
import re
from typing import Literal
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.abuse import limited_principal
from app.core.auth import Principal
from app.memory.session import SESSION_ID_RE

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    # the content page the user is looking at (a document `source` from GET /content); unknown/hidden pages are ignored
    page: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9_./-]+$")

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message is empty")
        return v

    @field_validator("session_id")
    @classmethod
    def _valid_sid(cls, v: str | None) -> str | None:
        if v is not None and not SESSION_ID_RE.match(v):
            raise ValueError("invalid session_id")
        return v


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a stray `comment` field is rejected, not stored
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    rating: Literal["up", "down"]
    # Fixed reasons only: free-text comments would be an uncontrolled PII channel.
    reason: Literal["wrong", "unclear", "unhelpful", "unsafe", "other"] | None = None


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    intent: str
    grounded: bool
    sources: list[str] = []
    refused_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    router_tokens: int = 0
    model: str | None = None
    standalone_query: str | None = None
    request_id: str | None = None
    cache_hit: str | None = None
    tokens_total: int | None = None   # tokens this answer cost us (router + answer in + out)
    naive_tokens: int | None = None   # estimate: what a send-the-whole-page-and-chat bot would have sent


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request, p: Principal = Depends(limited_principal)) -> ChatResponse:
    done = None
    async for ev in request.app.state.service.events(p, body.message, body.session_id, body.page):
        if ev["type"] == "error":
            raise HTTPException(status_code=503, detail=ev["message"])
        if ev["type"] == "done":
            done = ev
    return ChatResponse(**{k: v for k, v in done.items() if k in ChatResponse.model_fields})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request, p: Principal = Depends(limited_principal)) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        async for ev in request.app.state.service.events(p, body.message, body.session_id, body.page):
            t = ev.pop("type")
            yield _sse(t, ev)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/chat/sessions/{session_id}", status_code=204)
async def clear_session(session_id: str, request: Request, p: Principal = Depends(limited_principal)) -> None:
    if not SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=422, detail="invalid session_id")
    await request.app.state.service.sessions.delete(p.user_id, session_id)


@router.post("/chat/feedback", status_code=204)
async def feedback(body: FeedbackRequest, request: Request, p: Principal = Depends(limited_principal)) -> None:
    ok = await request.app.state.service.tracker.feedback(p, body.request_id, body.rating, body.reason)
    if not ok:
        raise HTTPException(status_code=404, detail="Request not found")
