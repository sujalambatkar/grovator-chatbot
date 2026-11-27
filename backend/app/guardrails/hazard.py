"""Wiring hazards a student describes ("I connected GND to 5V") or asks about ("can I connect GND to 5V?").

Decided in code, 0 tokens, BEFORE any other answer: the student is told directly to stop / not to do it, then (when a sensor is named) shown the
correct wiring. General electronics safety, not Grovator documentation.
"""
import re
from dataclasses import dataclass

_GND = r"(?:gnd|ground|0 ?v|negative|-ve)"
_PWR = r"(?:vcc|vin|vdd|5 ?v|3\.3 ?v|3v3|12 ?v|24 ?v|positive|\+ve)"
_FILL = r"(?:\s+(?:pin|wire|terminal|line|pad))?\s+(?:is |was |has been |got |are |were |i )?(?:connected |wired |attached |plugged |joined |hooked |shorted |soldered |touching |touches )?"
_LINK = r"(?:to|with|into|on)\s+(?:the |my |a |same )?"
_SIGNAL = r"(?:gpio ?-?\d+|data(?: ?pin)?|signal(?: ?pin)?|d0|do\b|ao\b|trig(?:ger)?|echo|rx|tx|sda|scl|analog pin|digital pin|input pin)"
_HIGH = r"(?:5 ?v|12 ?v|24 ?v|vcc|vin)"

_QUESTION = re.compile(r"^\W*(?:how (?:do|can|should|would) (?:i|we)|connect|wire|attach|plug|hook|join|put|link)\b|\?|\b(?:can|could|should|may|would|will|is it (?:ok|okay|safe|fine|allowed)|what if|what happens)\b[^.!]{0,40}\b(?:i|we)\b|\bis it (?:ok|okay|safe|fine)\b", re.I)


@dataclass
class Hazard:
    id: str
    fix: str          # what to do now (for "I did this")
    never: str        # why not, and what to do instead (for "can I do this?")
    fire: bool = False


_RULES: list[tuple[str, re.Pattern, Hazard]] = [
    ("short", re.compile(rf"\b{_GND}\b{_FILL}{_LINK}{_PWR}\b|\b{_PWR}\b{_FILL}{_LINK}{_GND}\b|\bshort[- ]?circuit(?:ed)?\b|\bshorted\b", re.I),
     Hazard("short_circuit",
            "Connecting GND (ground) to 5V, 3.3V, VCC or any power pin is a short circuit. It can burn the board, destroy the sensor, make parts very hot and hurt you. "
            "Unplug the USB or power cable first. Then move the wire that joins GND to the power pin. Do not switch the kit on again until your teacher has checked it. "
            "If anything smells burnt or feels hot, do not touch it and tell your teacher.",
            "Never connect GND (ground) to 5V, 3.3V, VCC or any power pin. That is a short circuit: it can burn the board, destroy the sensor, make parts very hot and hurt you. "
            "GND only goes to GND. Power (VCC) only goes to the voltage your sensor needs.")),
    ("power_to_signal", re.compile(rf"\b{_HIGH}\b{_FILL}{_LINK}{_SIGNAL}", re.I),
     Hazard("power_to_signal",
            "5V (or VCC) must never go to a GPIO or signal pin. The ESP32 pins only accept 3.3V, so 5V on a signal pin can permanently damage the board. "
            "Unplug the USB or power cable first, then move that wire. Only the sensor's signal wire (data, DO, TRIG, ECHO...) goes to a GPIO pin.",
            "Never connect 5V, 12V or VCC to a GPIO or signal pin. The ESP32 pins only accept 3.3V, so a higher voltage there can permanently damage the board. "
            "Only the sensor's signal wire goes to a GPIO pin.")),
    ("overvoltage", re.compile(rf"\b(?:12 ?v|24 ?v|9 ?v|battery)\b{_FILL}{_LINK}(?:the )?(?:esp32|esp 32|board|controller|gateway|gpio ?-?\d+|3\.3 ?v|5 ?v|vcc|vin|sensor)", re.I),
     Hazard("overvoltage",
            "Too much voltage can destroy the board and the sensor. Unplug the power now and check your teacher's wiring sheet before you try again. "
            "Use only the voltage that is written for your sensor.",
            "Do not connect 9V, 12V, 24V or a battery straight to the board or to a sensor that expects 3.3V or 5V. Too much voltage can destroy it. "
            "Use only the voltage written for your sensor, and ask your teacher first.")),
    ("heat", re.compile(r"\b(?:smok(?:e|ing)|burning smell|smells? (?:like )?(?:burn\w*|plastic|hot)|spark(?:s|ing|ed)?|melt(?:ing|ed)|very hot|got hot|getting hot|too hot to touch|swoll?en|swelling|bulging|puffed)\b", re.I),
     Hazard("overheating",
            "Unplug the USB or power cable right now, if you can do it without touching the hot part. Do not touch the board or the wires until they are cool. "
            "Tell your teacher now. Do not use it again until an adult has checked it.",
            "")),
    ("fire", re.compile(r"\b(?:caught fire|on fire|is burning|flames? (?:came|coming)|fire started)\b", re.I),
     Hazard("fire",
            "Step away from it now. Tell an adult straight away and follow your school's fire rules. Do not try to put out an electrical fire with water. "
            "If you are alone, leave the room and call the emergency number.",
            "", fire=True)),
    ("shock", re.compile(r"\b(?:got|received|had) (?:a |an )?(?:electric )?shock\b|\bshocked me\b|\belectric shock\b|\belectrocuted\b", re.I),
     Hazard("shock",
            "If someone is still touching the wire or the device, do not touch them: switch the power off or unplug it first, if you can do it safely. "
            "Tell an adult right now, even if you feel fine. Do not use the kit again until it has been checked.",
            "")),
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("-", " ").replace("_", " ").replace("5 volt", "5v").replace("3.3 volt", "3.3v")).strip()


def detect_hazard(message: str) -> tuple[Hazard, bool] | None:
    """(hazard, is_question). is_question: the student is asking whether they may do it, not saying they already did."""
    t = _norm(message)
    for _name, rx, hz in _RULES:
        if rx.search(t):
            if hz.id == "short_circuit" and re.search(r"\b(?:avoid|prevent|stop|why|what is|what's|explain|define|meaning)\b[^.?!]{0,30}\bshort[- ]?circuit", t) and not re.search(rf"\b{_GND}\b", t):
                continue   # "what is a short circuit?" is a concept question
            return hz, bool(_QUESTION.search(message)) and bool(hz.never)
    return None
