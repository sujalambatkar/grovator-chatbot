"""15 hand-written Q&A pairs: the expected source doc must appear in the top-4 hits."""
import pytest

from app.config import get_settings
from tests.conftest import make_principal

QA = [
    ("student", "How do I log in to Grovator?", "student/getting-started.md"),
    ("student", "I forgot my password, what do I do?", "student/getting-started.md"),
    ("student", "What is block coding?", "student/block-coding-basics.md"),
    ("student", "My blocks are not running, what's wrong?", "student/block-coding-basics.md"),
    ("student", "How do I make the robot repeat something 4 times?", "student/loops-and-conditions.md"),
    ("student", "What is a variable in blocks?", "student/loops-and-conditions.md"),
    ("student", "How do I stop the robot before it hits a wall?", ("student/loops-and-conditions.md", "lessons/rob-3-distance-sensor.md", "blocks/control-blocks.md")),
    ("student", "What sensors come in the IoT kit?", "student/iot-kit.md"),
    ("student", "The IoT kit will not connect to my computer", "student/iot-kit.md"),
    ("student", "What parts are in the Robotics Foundation kit?", "student/robotics-kit.md"),
    ("student", "My robot is not moving", "student/robotics-kit.md"),
    ("student", "How do I make the robot follow a black line?", "student/robotics-kit.md"),
    ("student", "Where are my saved programs?", "student/projects-and-help.md"),
    ("teacher", "How do I reset a student's password?", "teacher/batches-and-reports.md"),
    ("admin", "How do I assign a teacher to a batch?", "admin/school-administration.md"),
]


@pytest.mark.parametrize("role,question,expected", QA)
async def test_expected_source_in_top4(retriever, role, question, expected):
    res = await retriever.retrieve(question, make_principal(role), k=4)
    sources = [h.source for h in res.hits]
    ok = any(e in sources for e in ((expected,) if isinstance(expected, str) else expected))
    assert ok, f"{question!r} -> {sources}"
    assert res.confidence >= get_settings().min_retrieval_score


OFF_TOPIC = ["What is the capital of France?", "Who won the cricket world cup?", "Write me a poem about pizza"]


@pytest.mark.parametrize("q", OFF_TOPIC)
async def test_off_topic_below_threshold(retriever, q):
    res = await retriever.retrieve(q, make_principal("student"), k=4)
    assert res.confidence < get_settings().min_retrieval_score, res.confidence
