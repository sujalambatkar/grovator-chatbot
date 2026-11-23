"""Allow-listed, read-only, schema-validated tool execution.

The LLM may only propose {tool, args}. Args are Pydantic-validated (extra fields forbidden, so a smuggled
`org_id`/`user_id` is a validation error), names are resolved ONLY inside the caller's scope, and every returned
record is re-checked by policy. "Denied" and "not found" are indistinguishable to avoid enumeration.
"""
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.auth import Role
from app.llm.prompts import TOOL_FAIL_REPLY
from app.tools.adapter import PlatformDataAdapter
from app.tools.policy import ROLE_TOOLS, can_view_batch, can_view_student
from app.tools.types import Scope

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'_-]{0,59}$")
_SELF = {"me", "my", "myself", "i", "mine"}


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _name(v: str) -> str:
    if not _NAME.match(v):
        raise ValueError("invalid name")
    return v


class ListMyBatchesArgs(_Args):
    pass


class BatchSummaryArgs(_Args):
    batch: str

    _v = field_validator("batch")(_name)


class StudentReportArgs(_Args):
    student: str
    subject: str | None = Field(default=None, max_length=40)

    _v = field_validator("student")(_name)

    @field_validator("subject")
    @classmethod
    def _subject(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[A-Za-z ]{1,40}$", v):
            raise ValueError("invalid subject")
        return v


class ScheduleArgs(_Args):
    batch: str | None = None

    @field_validator("batch")
    @classmethod
    def _b(cls, v: str | None) -> str | None:
        return None if v is None else _name(v)


TOOLS: dict[str, type[_Args]] = {
    "list_my_batches": ListMyBatchesArgs, "get_batch_summary": BatchSummaryArgs,
    "get_student_report": StudentReportArgs, "get_schedule": ScheduleArgs,
}


@dataclass
class ToolResult:
    ok: bool
    text: str
    reason: str | None = None


def _fail(reason: str, text: str = TOOL_FAIL_REPLY) -> ToolResult:
    return ToolResult(False, text, reason)


async def run_tool(tool: str, raw_args: object, scope: Scope, adapter: PlatformDataAdapter) -> ToolResult:
    if tool not in TOOLS:
        return _fail("unknown_tool")
    if tool not in ROLE_TOOLS[scope.role]:
        return _fail("tool_not_allowed")
    try:
        args = TOOLS[tool].model_validate(raw_args if isinstance(raw_args, dict) else {})
    except ValidationError:
        return _fail("invalid_args")
    return await _HANDLERS[tool](args, scope, adapter)


async def _batches(scope: Scope, adapter: PlatformDataAdapter):
    return [b for b in await adapter.list_batches(scope) if can_view_batch(scope, b)]


async def _list_my_batches(_a, scope, adapter) -> ToolResult:
    bs = await _batches(scope, adapter)
    if not bs:
        return _fail("none_visible", "You don't have any batches assigned yet.")
    return ToolResult(True, "Your batches: " + "; ".join(f"{b.name} ({b.subject}, {b.student_count} students)" for b in bs))


async def _batch_summary(a: BatchSummaryArgs, scope, adapter) -> ToolResult:
    found = [b for b in await adapter.find_batches(scope, a.batch) if can_view_batch(scope, b)]
    if not found:
        return _fail("not_found")
    if len(found) > 1:
        return _fail("ambiguous", "Several batches match: " + ", ".join(b.name for b in found) + ". Which one?")
    b = found[0]
    return ToolResult(True, f"Batch {b.name}: {b.subject}, {b.student_count} students.")


async def _own_student(scope: Scope, adapter):
    s = await adapter.get_student(scope, scope.user_id)
    return s if s and can_view_student(scope, s) else None


async def _student_report(a: StudentReportArgs, scope, adapter) -> ToolResult:
    if a.student.lower() in _SELF:
        if scope.role is not Role.student:
            return _fail("self_not_student", "Please tell me which student you mean, by name.")
        candidates = [s for s in [await _own_student(scope, adapter)] if s]
    else:
        candidates = [s for s in await adapter.find_students(scope, a.student) if can_view_student(scope, s)]
    if not candidates:
        return _fail("not_found")
    if len(candidates) > 1:
        return _fail("ambiguous", "Several students match: " + ", ".join(s.name for s in candidates) + ". Which one?")
    s = candidates[0]
    reports = await adapter.get_reports(scope, s.id)
    if a.subject:
        reports = [r for r in reports if a.subject.lower() in r.subject.lower()]
    if not reports:
        return _fail("no_report", f"There is no report data for {s.name} yet.")
    lines = "; ".join(f"{r.subject}: {r.score_pct}% (labs {r.labs_done}/{r.labs_total})" for r in reports)
    return ToolResult(True, f"{s.name}: {lines}")


async def _schedule(a: ScheduleArgs, scope, adapter) -> ToolResult:
    if scope.role is Role.student:
        me = await _own_student(scope, adapter)
        batches = [] if me is None else [me.batch_id]
        if a.batch:  # students can't pick batches
            return _fail("not_found")
    else:
        visible = await _batches(scope, adapter)
        if a.batch:
            visible = [b for b in visible if a.batch.lower() in b.name.lower() or a.batch.lower() == b.id]
        batches = [b.id for b in visible]
    sessions = [s for b in batches for s in await adapter.get_schedule(scope, b)]
    if not sessions:
        return _fail("not_found")
    sessions.sort(key=lambda s: s.date)
    return ToolResult(True, "Upcoming sessions: " + "; ".join(
        f"{s.date} {s.subject} - {s.topic} ({s.kit})" for s in sessions[:5]))


_HANDLERS = {
    "list_my_batches": _list_my_batches, "get_batch_summary": _batch_summary,
    "get_student_report": _student_report, "get_schedule": _schedule,
}
