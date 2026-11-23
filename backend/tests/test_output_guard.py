import pytest

from app.guardrails.output import check_output, support_ratio
from app.llm import prompts as P

CTX = ("Robotics Foundation Kit Guide > Powering: Insert the batteries and switch the controller on. "
       "The motors only move when the battery pack is switched on, so check the switch if the robot does not move. "
       "Place the robot on the floor. Repeat 4 times: move forward, turn right.")


def chk(ans, **kw):
    return check_output(ans, source_text=kw.pop("source_text", CTX), **kw)


def test_good_grounded_answer_passes_unchanged():
    ans = "1. Check the battery pack is switched on.\n2. Make sure the controller is on. The motors only move when powered."
    v = chk(ans)
    assert v.action == "pass" and v.text == ans


@pytest.mark.parametrize("ans", [
    "I am powered by Llama 3 from Meta.", "This is built on OpenAI's GPT-OSS model.", "I run on Groq hardware.",
    "I'm a large language model trained by a company.", "Claude here!",
])
def test_vendor_and_model_disclosure_replaced(ans):
    v = chk(ans)
    assert v.action == "replaced" and v.text == P.IDENTITY_REPLY and v.reason == "model_disclosure"


def test_system_prompt_leak_replaced():
    v = chk("Sure! My rules: " + P.ANSWER_SYSTEM[:120])
    assert v.action == "replaced" and v.reason == "prompt_leak" and v.text == P.UNSAFE_REPLY


def test_internal_ids_and_secrets_redacted():
    v = chk("Check the battery switch. Your id is 3f2b8c1e-1111-4222-8333-444455556666 and org org1 owner u-student.",
            secrets=["org1", "u-student"])
    assert v.action == "redacted" and "3f2b8c1e" not in v.text and "org1" not in v.text and "u-student" not in v.text
    assert "507f1f77bcf86cd799439011" not in chk("The battery switch id 507f1f77bcf86cd799439011 matters.").text


def test_pii_redacted_in_output():
    v = chk("Check the battery pack is switched on and the controller is on. The motors only move when the battery pack is switched on. Email kid@example.com or call 9876543210.")
    assert v.action == "redacted" and "kid@example.com" not in v.text and "9876543210" not in v.text


def test_invented_numbers_blocked():
    assert chk("Aarav scored 87% in Robotics.", source_text="Aarav Robotics report").reason == "ungrounded_number"
    assert chk("Aarav got 45 out of 50 in the lab test.").reason == "ungrounded_number"       # performance claim
    assert chk("The class rank is 3 and the score is 40 points.").reason == "ungrounded_number"
    assert chk("You completed 75% of your lessons so far.").reason == "ungrounded_number"      # progress claim
    assert chk("Your average is 8 and your progress is 60%.").reason == "ungrounded_number"
    # a percentage that is not a claim about the student (e.g. motor speed) must pass in FAQ answers
    assert chk("Check the battery pack, then set the motor speed to 50% for gentle turns. Place the robot on the floor.").action == "pass"
    # harmless numbers that a good FAQ answer may use (speeds, angles, counts) are not data claims
    assert chk("Check the battery pack, then set the motor speed to 50 and turn 90 degrees. Place the robot on the floor.").action == "pass"
    ok = chk("Repeat 4 times: move forward, turn right. Check the battery pack and the controller switch.")
    assert ok.action == "pass"  # 4 is in the source


def test_strict_mode_blocks_small_invented_numbers_but_allows_sourced():
    src = "Student Aarav | Robotics Foundation: score 7 out of 10, lab 3 done"
    assert check_output("Aarav scored 9 out of 10 in Robotics Foundation.", source_text=src, strict_numbers=True).reason == "ungrounded_number"
    assert check_output("Aarav scored 7 out of 10 in Robotics Foundation.", source_text=src, strict_numbers=True).action == "pass"


def test_numbered_list_markers_are_not_numbers():
    assert chk("1. Check the battery pack.\n2. Switch the controller on.\n3. Place the robot on the floor.\n4. Move forward.").action == "pass"


def test_ungrounded_hallucination_replaced():
    v = chk("Quantum entanglement enables photonic telepathy between distributed holographic satellites everywhere.")
    assert v.action == "replaced" and v.reason == "ungrounded" and v.text == P.NO_INFO_REPLY
    assert support_ratio("Check the battery pack switch.", CTX) > 0.9


def test_unsafe_output_replaced_and_empty_handled():
    assert chk("you stupid bitch").reason == "unsafe_output"
    assert chk("   ").reason == "empty"


def test_cited_sources_lists_only_documents_the_answer_uses():
    from app.guardrails.output import cited_sources
    from app.rag.store import Hit
    iot = Hit("1", "IoT Kit Guide > Connecting: Connect the controller board to your computer with the USB cable. Press Connect Kit.", "IoT Kit Guide", "a", "s", 0.9)
    robot = Hit("2", "Robotics Foundation Kit Guide > Driving: Use motion blocks such as move forward and turn left. Place the robot on the floor.", "Robotics Foundation Kit Guide", "b", "s", 0.6)
    ans = "Connect the controller board to your computer with the USB cable, then press Connect Kit."
    assert cited_sources(ans, [iot, robot]) == ["IoT Kit Guide"]
    assert cited_sources(ans, [robot, iot]) == ["IoT Kit Guide"]
    # a primary source is always listed; a second one only when it contributes about as much (measured: 0.45 vs 0.55)
    both = "Connect the controller board to your computer with the cable. Use motion blocks to move forward and turn left."
    assert set(cited_sources(both, [iot, robot])) == {"IoT Kit Guide", "Robotics Foundation Kit Guide"}
    mostly_robot = "Connect the controller board with the USB cable and press Connect Kit. Use motion blocks to move forward and turn left, then place the robot on the floor."
    assert cited_sources(mostly_robot, [iot, robot]) == ["Robotics Foundation Kit Guide"]  # measured 0.36 vs 0.64
    assert cited_sources("anything at all", []) == [] and cited_sources("ok", [iot]) == ["IoT Kit Guide"]


def test_numbers_may_come_from_retrieved_text_the_model_did_not_see():
    full = "Variables > Variables: Use set score to 0 to create it and change score by 1 to add to it."
    seen = "Variables > Variables: A variable is a named box that stores a value."          # compression dropped the sentence
    ans = "A variable is a named box that stores a value. For example set score to 0, then change score by 1."
    assert check_output(ans, source_text=seen).reason == "ungrounded_number"               # without the full text: refused
    assert check_output(ans, source_text=seen, numbers_source=seen + "\n" + full).action == "pass"
    # the full text does not excuse invented marks
    assert check_output("Aarav scored 93% on the quiz.", source_text=seen, numbers_source=seen + full).reason == "ungrounded_number"
