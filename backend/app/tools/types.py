"""Platform data shapes. ASSUMED (see docs/chatbot/DISCOVERY.md): replace with the real Grovator schema."""
from dataclasses import dataclass

from app.core.auth import Principal, Role


@dataclass(frozen=True)
class Scope:
    """Derived from the JWT only. Never constructed from message text or LLM output."""
    user_id: str
    role: Role
    org_id: str
    school_id: str | None
    batch_ids: tuple[str, ...]

    @classmethod
    def from_principal(cls, p: Principal) -> "Scope":
        return cls(p.user_id, p.role, p.org_id, p.school_id, tuple(p.batch_ids))


@dataclass
class Batch:
    id: str
    name: str
    org_id: str
    school_id: str
    subject: str
    student_count: int


@dataclass
class Student:
    id: str
    name: str
    org_id: str
    school_id: str
    batch_id: str


@dataclass
class SubjectReport:
    subject: str
    score_pct: int
    labs_done: int
    labs_total: int


@dataclass
class Session:
    batch_id: str
    date: str
    subject: str
    topic: str
    kit: str
