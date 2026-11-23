"""JWT -> Principal. Scope comes ONLY from the token, never from message text or the LLM."""
from enum import Enum

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.config import Settings, get_settings


class Role(str, Enum):
    superadmin = "superadmin"
    admin = "admin"
    teacher = "teacher"
    student = "student"


class Principal(BaseModel):
    user_id: str
    role: Role
    org_id: str
    school_id: str | None = None
    batch_ids: list[str] = Field(default_factory=list)


_bearer = HTTPBearer(auto_error=False)


def decode_token(token: str, settings: Settings) -> Principal:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
                            options={"require": ["exp", "sub"]})  # a token that never expires is never valid
        return Principal(
            user_id=claims["sub"],
            role=claims["role"],
            org_id=claims["org_id"],
            school_id=claims.get("school_id"),
            batch_ids=claims.get("batch_ids", []),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or missing credentials") from exc


def get_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if creds is None:
        raise HTTPException(status_code=401, detail="Invalid or missing credentials")
    return decode_token(creds.credentials, settings)
