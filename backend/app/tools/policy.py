"""Authorization policy. Applied by code to EVERY record an adapter returns; the LLM is never consulted.

teacher -> their batches and the students in them; admin -> their school; superadmin -> their org;
student -> self only. Tenant (org) must always match.
"""
from app.core.auth import Role
from app.tools.types import Batch, Scope, Student


def can_view_batch(scope: Scope, b: Batch) -> bool:
    if b.org_id != scope.org_id:
        return False
    match scope.role:
        case Role.superadmin:
            return True
        case Role.admin:
            return scope.school_id is not None and b.school_id == scope.school_id
        case Role.teacher:
            return b.id in scope.batch_ids
        case _:  # student: handled via own student record, not batch listing
            return False


def can_view_student(scope: Scope, s: Student) -> bool:
    if s.org_id != scope.org_id:
        return False
    match scope.role:
        case Role.superadmin:
            return True
        case Role.admin:
            return scope.school_id is not None and s.school_id == scope.school_id
        case Role.teacher:
            return s.batch_id in scope.batch_ids
        case _:
            return s.id == scope.user_id


ROLE_TOOLS: dict[Role, frozenset[str]] = {
    Role.student: frozenset({"get_student_report", "get_schedule"}),
    Role.teacher: frozenset({"list_my_batches", "get_batch_summary", "get_student_report", "get_schedule"}),
    Role.admin: frozenset({"list_my_batches", "get_batch_summary", "get_student_report", "get_schedule"}),
    Role.superadmin: frozenset({"list_my_batches", "get_batch_summary", "get_student_report", "get_schedule"}),
}
