"""MOCK platform data for dev/tests. Deliberately NAIVE: ignores `scope` and returns matches from every tenant,
so tests prove that the policy layer in the executor (not the adapter) enforces access. A real adapter should
also filter by scope (defence in depth)."""
from app.tools.types import Batch, Scope, Session, Student, SubjectReport

BATCHES = [
    Batch("b1", "B-12", "org1", "sch1", "Robotics Foundation", 2),
    Batch("b2", "B-13", "org1", "sch1", "IoT", 1),
    Batch("b3", "C-01", "org1", "sch2", "IoT", 1),
    Batch("b9", "Z-99", "org2", "sch9", "Robotics Foundation", 1),
]
STUDENTS = [
    Student("s1", "Aarav Mehta", "org1", "sch1", "b1"),
    Student("s2", "Riya Sharma", "org1", "sch1", "b1"),
    Student("s3", "Kabir Singh", "org1", "sch1", "b2"),
    Student("s4", "Meera Iyer", "org1", "sch2", "b3"),
    Student("s5", "Zoya Khan", "org2", "sch9", "b9"),
]
REPORTS = {
    "s1": [SubjectReport("Robotics Foundation", 82, 7, 10), SubjectReport("IoT", 74, 6, 10)],
    "s2": [SubjectReport("Robotics Foundation", 91, 9, 10)],
    "s3": [SubjectReport("IoT", 66, 5, 10)],
    "s4": [SubjectReport("IoT", 58, 4, 10)],
    "s5": [SubjectReport("Robotics Foundation", 99, 10, 10)],
}
SESSIONS = [
    Session("b1", "2026-12-01", "Robotics Foundation", "Line following", "Robotics kit"),
    Session("b1", "2026-12-04", "Robotics Foundation", "Distance sensor", "Robotics kit"),
    Session("b2", "2026-12-02", "IoT", "Temperature sensor", "IoT kit"),
    Session("b3", "2026-12-03", "IoT", "Light sensor", "IoT kit"),
    Session("b9", "2026-12-05", "Robotics Foundation", "Motors", "Robotics kit"),
]


class MockAdapter:
    async def list_batches(self, scope: Scope) -> list[Batch]:
        return list(BATCHES)

    async def find_batches(self, scope: Scope, query: str) -> list[Batch]:
        q = query.lower()
        return [b for b in BATCHES if q in b.name.lower() or q == b.id]

    async def find_students(self, scope: Scope, query: str) -> list[Student]:
        q = query.lower()
        return [s for s in STUDENTS if q in s.name.lower() or q == s.id]

    async def get_student(self, scope: Scope, student_id: str) -> Student | None:
        return next((s for s in STUDENTS if s.id == student_id), None)

    async def get_reports(self, scope: Scope, student_id: str) -> list[SubjectReport]:
        return REPORTS.get(student_id, [])

    async def get_schedule(self, scope: Scope, batch_id: str) -> list[Session]:
        return [s for s in SESSIONS if s.batch_id == batch_id]
