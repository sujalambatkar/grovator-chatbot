"""Demo-only auto login so the demo page needs no token box. Disabled unless DEMO_MODE=true, and never in production."""
import time
from typing import Literal

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoRequest(BaseModel):
    role: Literal["student", "teacher"] = "student"


@router.post("/session")
async def demo_session(body: DemoRequest, request: Request) -> dict:
    s = request.app.state.settings
    if not s.demo_mode or s.environment.lower() == "production":
        raise HTTPException(status_code=404, detail="Not found")
    user = {"student": ("s1", "Aarav (student)"), "teacher": ("t1", "Ms. Rao (teacher)")}[body.role]
    claims = {"sub": user[0], "role": body.role, "org_id": "org1", "school_id": "sch1", "batch_ids": ["b1"],
              "exp": int(time.time()) + 12 * 3600}
    return {"token": jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm), "role": body.role, "name": user[1]}
