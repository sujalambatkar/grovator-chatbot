"""Authorization tests. MockAdapter is naive (returns every tenant's data), so any isolation here comes from policy."""
import pytest

from app.core.auth import Role
from app.tools.executor import run_tool
from app.tools.mock_adapter import MockAdapter
from app.tools.types import Scope

A = MockAdapter()


def scope(role, user="u1", org="org1", school="sch1", batches=()):
    return Scope(user, Role(role), org, school, tuple(batches))


TEACHER = scope("teacher", "t1", batches=["b1"])          # teaches B-12 only
ADMIN = scope("admin", "a1", school="sch1")               # school sch1
SUPER = scope("superadmin", "root", school=None)          # org1
STUDENT = scope("student", "s1", batches=["b1"])          # Aarav


async def ok(tool, args, sc):
    r = await run_tool(tool, args, sc, A)
    assert r.ok, (tool, args, r)
    return r.text


async def denied(tool, args, sc):
    r = await run_tool(tool, args, sc, A)
    assert not r.ok, (tool, args, sc.role, r.text)
    return r


# ---- positive paths ----
async def test_teacher_sees_own_batch_students_and_schedule():
    assert "B-12" in await ok("list_my_batches", {}, TEACHER)
    assert "82%" in await ok("get_student_report", {"student": "Aarav"}, TEACHER)
    assert "Line following" in await ok("get_schedule", {}, TEACHER)
    assert "2 students" in await ok("get_batch_summary", {"batch": "B-12"}, TEACHER)


async def test_student_sees_only_self():
    assert "82%" in await ok("get_student_report", {"student": "me"}, STUDENT)
    assert "82%" in await ok("get_student_report", {"student": "Aarav", "subject": "Robotics"}, STUDENT)
    txt = await ok("get_schedule", {}, STUDENT)
    assert "Line following" in txt and "Temperature" not in txt


async def test_admin_sees_school_superadmin_sees_org():
    assert "66%" in await ok("get_student_report", {"student": "Kabir"}, ADMIN)    # b2 is in sch1
    names = await ok("list_my_batches", {}, ADMIN)
    assert "B-12" in names and "B-13" in names and "C-01" not in names             # sch2 excluded
    assert "58%" in await ok("get_student_report", {"student": "Meera"}, SUPER)     # sch2 but org1
    assert "Z-99" not in await ok("list_my_batches", {}, SUPER)                    # org2 never


# ---- cross-tenant / cross-scope denials ----
@pytest.mark.parametrize("sc,student", [
    (TEACHER, "Kabir"),      # other teacher's batch, same school
    (TEACHER, "Meera"),      # other school
    (TEACHER, "Zoya"),       # other org
    (ADMIN, "Meera"),        # other school
    (ADMIN, "Zoya"),         # other org
    (SUPER, "Zoya"),         # other org
    (STUDENT, "Riya"),       # classmate
    (STUDENT, "Kabir"),      # other batch
    (STUDENT, "Zoya"),
])
async def test_report_for_out_of_scope_student_is_denied_and_leaks_nothing(sc, student):
    r = await denied("get_student_report", {"student": student}, sc)
    assert r.reason == "not_found"
    assert not any(ch.isdigit() for ch in r.text)  # no scores in the denial text


async def test_denied_and_missing_are_indistinguishable():
    denied_ = await run_tool("get_student_report", {"student": "Zoya"}, TEACHER, A)
    missing = await run_tool("get_student_report", {"student": "Nobodyatall"}, TEACHER, A)
    assert (denied_.ok, denied_.text, denied_.reason) == (missing.ok, missing.text, missing.reason)


async def test_batches_and_schedule_scoping():
    assert (await denied("get_batch_summary", {"batch": "B-13"}, TEACHER)).reason == "not_found"
    assert (await denied("get_batch_summary", {"batch": "Z-99"}, SUPER)).reason == "not_found"
    sched = await ok("get_schedule", {}, TEACHER)
    assert "Temperature" not in sched and "Light sensor" not in sched and "Motors" not in sched
    assert (await denied("get_schedule", {"batch": "B-13"}, TEACHER)).reason == "not_found"
    assert (await denied("get_schedule", {"batch": "B-13"}, STUDENT)).reason == "not_found"  # students can't pick batches


async def test_same_user_id_other_org_gets_nothing():
    impostor = scope("teacher", "t1", org="org2", school="sch9", batches=["b1"])  # b1 belongs to org1
    assert (await denied("get_student_report", {"student": "Aarav"}, impostor)).reason == "not_found"
    assert (await denied("get_schedule", {}, impostor)).reason == "not_found"


async def test_ambiguous_name_asks_instead_of_guessing():
    r = await denied("get_student_report", {"student": "a"}, SUPER)  # matches several
    assert r.reason == "ambiguous" and "Which one" in r.text


# ---- allow-list, role gating, arg validation ----
@pytest.mark.parametrize("tool", ["delete_student", "run_query", "update_marks", "get_all_students", "", "list_my_batches; DROP"])
async def test_unknown_tools_rejected(tool):
    assert (await denied(tool, {}, SUPER)).reason == "unknown_tool"


async def test_student_cannot_use_staff_tools():
    assert (await denied("list_my_batches", {}, STUDENT)).reason == "tool_not_allowed"
    assert (await denied("get_batch_summary", {"batch": "B-12"}, STUDENT)).reason == "tool_not_allowed"


@pytest.mark.parametrize("args", [
    {"student": "Aarav", "org_id": "org2"}, {"student": "Aarav", "user_id": "s5"}, {"student": "Aarav", "role": "superadmin"},
    {"student": {"$ne": None}}, {"student": "$where: 1"}, {"student": "x" * 80}, {"student": ""}, {"student": "Aarav'; DROP"},
    {"student": ["Aarav", "Zoya"]}, {}, {"student": "Aarav", "subject": "IoT; rm"}, {"student": None},
])
async def test_invalid_or_smuggled_args_rejected(args):
    r = await denied("get_student_report", args, SUPER)
    assert r.reason == "invalid_args"


async def test_non_dict_args_and_extra_fields_on_other_tools():
    assert (await denied("list_my_batches", {"org_id": "org2"}, SUPER)).reason == "invalid_args"
    assert (await denied("get_schedule", {"batch": {"$gt": ""}}, SUPER)).reason == "invalid_args"
    assert (await denied("get_student_report", "Aarav", SUPER)).reason == "invalid_args"


async def test_self_pronoun_only_for_students():
    assert (await denied("get_student_report", {"student": "me"}, TEACHER)).reason == "self_not_student"


def test_adapter_protocol_is_read_only():
    import inspect
    from app.tools.adapter import PlatformDataAdapter
    writes = [n for n, _ in inspect.getmembers(PlatformDataAdapter, inspect.isfunction)
              if n.startswith(("create", "update", "delete", "set", "write", "add", "remove", "reset"))]
    assert writes == []
