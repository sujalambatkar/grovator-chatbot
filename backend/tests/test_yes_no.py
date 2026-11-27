"""A yes/no question is answered with the verdict FIRST ("**Yes.** ..."), then the details."""
import pytest

from app.chat.appkb import Entities
from tests.test_block_palette import KB


def ask(q):
    return KB.answer(q, KB.detect(q), Entities())


@pytest.mark.parametrize("q,start", [
    ("Do I need the Calculate Distance block?", "**Yes.**"),
    ("Is the Calculate CO value block needed?", "**Yes.**"),
    ("Is the Start block required?", "**No.**"),
    ("Do I need the Get Weight block?", "**Yes, if you want the reading.**"),
    ("Do I need the Set Buzzer (named) as Output at Pin block?", "**No, not for a single device.**"),
    ("Do I need the Pause block?", "**It depends.**"),
    ("Is there a block for the buzzer?", "**Yes.**"),
    ("Does the pulse oximeter use I2C?", "**Yes.**"),
    ("Does the ultrasonic sensor use I2C?", "**No.**"),
    ("Does the rainfall sensor use UART?", "**Yes.**"),
    ("Can I connect the ultrasonic sensor to the ESP32 kit?", "**Yes.**"),
])
def test_the_verdict_comes_first(q, start):
    r = ask(q)
    assert r is not None and r.text.startswith(start), (q, r.text[:120] if r else None)


def test_the_details_still_follow_the_verdict():
    r = ask("Do I need the Calculate Distance block?")
    assert "Where:" in r.text and "Ultrasonic Sensor" in r.text and "What it does" not in r.text   # verdict plus one line, not the whole block answer


def test_a_question_that_is_not_yes_no_gets_no_verdict():
    r = ask("Where is the Calculate Distance block?")
    assert not r.text.startswith("**Yes") and not r.text.startswith("**No")


def test_unknown_things_are_not_answered_yes_or_no():
    r = ask("Can I connect the BME280 to the Grovator controller?")
    assert r is not None and not r.text.startswith("**Yes")


@pytest.mark.parametrize("q,must,must_not", [
    ("What is the ultrasonic sensor?", ["What it is"], ["How to connect", "VCC", "Typical program"]),
    ("How do I connect the ultrasonic sensor?", ["How to connect", "TRIG"], ["What it is", "Typical program", "Stack"]),
    ("Where is the Get Weight block?", ["Sensors-ESP32"], ["What it does", "Returns the measured weight", "group, in order"]),
    ("What does the Get Weight block do?", ["Returns the measured weight"], ["Where:", "block 2 of 2"]),
    ("Does the pulse oximeter use I2C?", ["**Yes.**"], ["SDA", "How to connect", "Typical program"]),
    ("Which blocks do I need for the buzzer?", ["Turn On/Off Buzzer"], ["How to connect", "VCC", "Need the wiring"]),
])
def test_an_answer_contains_only_what_was_asked(q, must, must_not):
    r = ask(q)
    assert r is not None
    for m in must:
        assert m in r.text, (q, m)
    for m in must_not:
        assert m not in r.text, (q, m)


def test_both_parts_are_answered_when_both_were_asked():
    r = ask("How do I connect the ultrasonic sensor and which blocks do I need?")
    assert "How to connect" in r.text and "TRIG" in r.text and "Calculate Distance" in r.text
