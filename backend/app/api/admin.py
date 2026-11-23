"""Admin usage summary. admin -> their own school only; superadmin -> their org (optionally one school)."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.abuse import limited_principal
from app.core.auth import Principal, Role

router = APIRouter(prefix="/admin/chatbot", tags=["admin"])


@router.get("/usage")
async def usage(
    request: Request, days: int = Query(7, ge=1, le=31), school_id: str | None = Query(None, max_length=64),
    p: Principal = Depends(limited_principal),
) -> dict:
    if p.role not in (Role.admin, Role.superadmin):
        raise HTTPException(status_code=403, detail="Not allowed")
    if p.role is Role.admin:
        if school_id not in (None, p.school_id) or not p.school_id:
            raise HTTPException(status_code=403, detail="Not allowed")
        school_id = p.school_id
    return await request.app.state.service.tracker.summary(p.org_id, school_id, days)
