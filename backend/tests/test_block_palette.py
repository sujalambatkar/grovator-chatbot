"""The block sheet (201 blocks of the ESP32 block editor): where a block is, what a category / sensor has, what a block does."""
import pytest

from app.chat.appkb import AppKB, Entities
from ingest.import_block_sheet import EXPECTED_GENERAL, EXPECTED_TOTAL, build
from knowledge.loader import validate
from tests.test_generic_kb import GENERIC_DIR

K = validate(GENERIC_DIR)
KB = AppKB(K)


def ask(q: str):
    e = KB.detect(q)
    return KB.answer(q, e, Entities())


def test_sheet_totals_match_the_sheet_summary():
    sheet = [b for b in K.blocks.values() if b.order]
    assert len(sheet) == EXPECTED_TOTAL == len(build())
    for cat, n in EXPECTED_GENERAL.items():
        assert sum(1 for b in sheet if b.category == cat) == n
    assert sum(1 for b in sheet if b.category == "Sensors-ESP32") == 133
    assert all(not b.code and not b.verified for b in sheet)   # the sheet has no code; descriptions come from names


def test_sensor_blocks_follow_the_sheet_order():
    assert [K.blocks[b].title for b in K.sensors["alcohol_sensor"].blocks] == ["Set Alcohol Sensor as Input at Pin", "Get Alcohol Sensor Value from Pin"]
    assert len(K.sensors["pulseoximeter_max30102"].blocks) == 4


@pytest.mark.parametrize("q,must", [
    ("Which blocks do I need for the buzzer?", ["Set Buzzer as Output at Pin", "Turn On/Off Buzzer", "Sensors-ESP32 › Buzzer"]),
    ("Where is the Start block?", ["Common Actions-ESP32", "block 3 of 11"]),
    ("Where can I find the repeat block", ["Loops"]),
    ("What blocks are in the Math category?", ["17 blocks", "Random fraction"]),
    ("How many blocks are there?", ["201 blocks", "Mobile**: 12 blocks"]),
    ("Where is the Get Temperature block?", ["BME Sensor", "Climate Sensor", "Soil Sensor"]),
    ("what does the Get Weight block do", ["Returns the measured weight"]),
    ("Which blocks does the LoRa have?", ["LoRa Init", "LoRa Send"]),
    ("Which blocks do I need?", ["I know 201 blocks"]),
    ("Which blocks do I need for the pulse oximeter?", ["Initialize max30100", "Get SpO2"]),
    ("what does the Turn On Relay block do?", ["Relay", "Switches the specified relay on or off"]),
])
def test_block_questions_are_answered_from_the_sheet(q, must):
    r = ask(q)
    assert r is not None, q
    for m in must:
        assert m in r.text, (q, m, r.text)


def test_code_of_a_sheet_block_is_not_invented():
    r = KB._block_code_answer([K.blocks["alcohol_sensor_get_alcohol_sensor_value_from_pin"]])
    assert r.kind == "not_documented" and "no code" in r.text


def test_wiring_questions_are_not_taken_by_the_block_answers():
    r = ask("How do I connect the alcohol sensor?")
    assert r is not None and r.kind != "blocks" and r.kind != "block"


def test_a_named_block_beats_the_sensor_remembered_from_the_conversation():
    """"calculate co value block locate it" after a chat about the alcohol sensor must find the CO block, not list the alcohol blocks."""
    q = "calculate co value block locate it"
    e = KB.detect(q)
    e.sensors = ["alcohol_sensor"]   # what the follow-up logic adds from the previous turn
    r = KB.answer(q, e, Entities())
    assert r is not None and "Carbon Monoxide Sensor" in r.text and "Alcohol" not in r.text


def _with_stale(q: str, stale: str = "alcohol_sensor"):
    e = KB.detect(q)
    e.sensors = list(dict.fromkeys(e.sensors + [stale]))   # carried over from an earlier turn
    return KB.answer(q, e, Entities())


def test_every_sheet_block_is_found_by_its_title_even_with_a_stale_sensor():
    import re
    for b in (b for b in K.blocks.values() if b.order):
        r = _with_stale(f"where is the {re.sub(r'[(].*?[)]', '', b.title).strip()} block")
        assert r is not None and (b.title in r.text or b.sub_category in r.text or any(x["title"] == b.title for x in r.blocks)), b.title


def test_a_stale_sensor_never_answers_for_a_sensor_named_in_the_message():
    r = _with_stale("Which blocks do I need for the buzzer?")
    assert "Buzzer" in r.text and "Alcohol" not in r.text


def test_a_stale_sensor_does_not_answer_an_unmatched_block_question():
    assert _with_stale("locate Remainder of a block") is None


def test_a_real_follow_up_still_uses_the_conversation():
    r = _with_stale("which blocks does it need?")
    assert r is not None and "Alcohol Sensor" in r.text


@pytest.mark.parametrize("q", ["Show me the code of the Get Weight block", "What is the Python code behind the Turn On Buzzer block?",
                               "show me the code for the buzzer blocks", "give me the python code for the ultrasonic sensor"])
def test_code_requests_for_sheet_blocks_never_reach_the_model(q):
    r = ask(q)
    assert r is not None and r.kind == "not_documented" and "no code" in r.text, q


def test_block_code_phrase_still_means_blocks():
    r = ask("which block codes do I need for the buzzer")
    assert r is not None and r.kind == "blocks"
