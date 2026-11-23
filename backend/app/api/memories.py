"""User control over long-term memory. The user is always taken from the JWT, never from the URL/body."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.abuse import limited_principal
from app.core.auth import Principal

router = APIRouter(prefix="/memories", tags=["memories"])


class MemoryOut(BaseModel):
    id: str
    text: str
    type: str
    created_at: str
    last_used_at: str


class DeleteAllOut(BaseModel):
    deleted: int


def _store(request: Request):
    return request.app.state.service.memory.store


@router.get("", response_model=list[MemoryOut])
async def list_memories(request: Request, p: Principal = Depends(limited_principal)) -> list[MemoryOut]:
    return [MemoryOut(id=m.id, text=m.text, type=m.type, created_at=m.created_at, last_used_at=m.last_used_at)
            for m in await _store(request).list_all(p)]


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: str, request: Request, p: Principal = Depends(limited_principal)) -> None:
    try:
        uuid.UUID(memory_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Memory not found") from None
    if not await _store(request).delete(p, memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")


@router.delete("", response_model=DeleteAllOut)
async def delete_all_memories(request: Request, p: Principal = Depends(limited_principal)) -> DeleteAllOut:
    return DeleteAllOut(deleted=await _store(request).delete_all(p))
