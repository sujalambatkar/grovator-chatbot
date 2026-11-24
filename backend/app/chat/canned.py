"""Pre-written answers for the plain, most common questions: 0 LLM tokens, instant, identical every time.

Nothing here is typed by hand: every answer is rendered from the gateway dataset (the same records and generators the guides use), so editing the
JSON changes the answers. Matching is deliberately STRICT and rule-based (no embeddings): the question must be one short, simple question of a
known shape about ONE named device (or the device in focus). Anything with specifics of the student's own ("I connected TRIG to GPIO5"), several
questions in one, comparisons, "why/if/after/not working", or any other shape falls through to the model.
"""
import re
from dataclasses import dataclass

from app.rag.hardware import pretty

_WORDS = re.compile(r"[a-z0-9']+")
# anything that makes a question personal, compound or diagnostic: never answered from a template
_NOT_SIMPLE = re.compile(
    r"(?<![a-z\d])\d|\bgpio\s?\d|\b(?:and|or|vs|versus|compare|difference|between|but|because|if|why|when|after|before|next|then|wrong|error|not|n't|doesn|isn|"
    r"won|cannot|broken|stuck|connected|connecting to|used|using|tried|my|mine|i have|i am|i'm|code|program|upload|run|read|reading|safe|damage)\b", re.I)

_WHAT = re.compile(r"^(?:what(?:'s| is| are)|whats|define|explain|tell me about|what does)\s+(?:an?\s+|the\s+)?(?P<x>.+?)(?:\s+do|\s+mean)?$", re.I)
_HOW_WORKS = re.compile(r"^how (?:does|do)\s+(?:an?\s+|the\s+)?(?P<x>.+?)\s+work$", re.I)
_CONNECT = re.compile(
    r"^(?:how (?:do|can|should) i |how to |how do you |can you (?:tell|show) me how to |show me how to |i want to |help me )?"
    r"(?:connect|wire|hook up|attach|plug in|plug|set up|setup|install)\b|\bwiring\b|\bconnection\b|\bsteps? (?:to|for)\b", re.I)
_PINS = re.compile(r"\bpins?\b|\bpinout\b|\bwhich (?:gpio|connector)\b", re.I)
_PRONOUN = re.compile(r"\b(?:it|this|this one|that one|the sensor|the module)\b", re.I)
_LIST = re.compile(r"^(?:which|what)\s+(?:sensors|modules|devices|components|peripherals)\b.*\b(?:connect|support|use|work|available|documented)|"
                   r"^(?:list|show)\s+(?:all\s+)?(?:the\s+)?(?:sensors|modules|devices)", re.I)
_GATEWAY = re.compile(r"^(?:what(?:'s| is)|tell me about|explain)\s+(?:the\s+)?(?:grovator\s+)?(?:esp32\s+)?(?:iot\s+)?gateway$", re.I)
_RULES = re.compile(r"^(?:what are |show |list )?(?:the )?(?:gateway )?wiring rules\??$", re.I)
_IFACE_KEYWORDS = {"digital": "digital_gpio", "analog": "analog_input", "i2c": "i2c", "uart": "uart", "keypad": "keypad", "stepper": "stepper_motor"}


@dataclass
class CannedAnswer:
    text: str
    sources: list[str]
    kind: str


def _clean(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip().rstrip("?!. ").strip())


class CannedAnswers:
    def __init__(self, gateway) -> None:
        self.gw = gateway

    # ------------------------------------------------------------------ rendering (all from the data)
    def _title(self, key: str) -> str:
        return f"{pretty(key)}: Connection Guide"

    def _where(self, key: str) -> str:
        """One line: where this device connects on the gateway."""
        dev = self.gw.devices[key]
        if "pins" in dev:
            return "It connects with " + ", ".join(f"{p} to {t}" for p, t in dev["pins"].items()) + "."
        if "signal" in dev:
            return f"Its {dev['signal']} signal pin goes to {dev.get('connect_to', 'the gateway')}."
        return f"It connects to the {dev.get('connect_to', 'gateway')}."

    def what(self, key: str) -> CannedAnswer | None:
        concept = self.gw.concept_for(key)
        if not concept:
            return None  # the dataset has no definition for this device: a model may explain it in general terms, a template must not
        return CannedAnswer(f"{concept} On the {self.gw.name}: {self._where(key)}", [self._title(key), "Electronics Concepts"], "what")

    def connect(self, key: str) -> CannedAnswer:
        dev = self.gw.devices[key]
        steps = self.gw.device_steps(key)
        text = f"To connect the {pretty(key)} to the {self.gw.name}:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
        ik = self.gw.interface_key(key, dev)
        if ik == "digital_gpio" or "digital" in str(dev.get("interface", "")):
            text += "\nDigital GPIO pins on this gateway: " + ", ".join(self.gw.interfaces["digital_gpio"].get("pins", [])) + "."
        return CannedAnswer(text, [self._title(key)], "connect")

    def pins(self, key: str) -> CannedAnswer:
        dev = self.gw.devices[key]
        ik = self.gw.interface_key(key, dev)
        parts = []
        if "pins" in dev:
            parts.append("Module pins: " + "; ".join(f"{p} to {t}" for p, t in dev["pins"].items()) + ".")
        elif "signal" in dev:
            parts.append(f"Signal pin on the module: {dev['signal']}; it connects to {dev.get('connect_to', 'the gateway')}.")
        if ik:
            parts.append(f"Gateway {pretty(ik)}: " + "; ".join(l[2:].rstrip(".") for l in self.gw._iface_lines(ik)) + ".")
        else:
            parts.append(f"It connects to the {dev.get('connect_to', 'gateway')}; the dataset lists no GPIO pins for it.")
        return CannedAnswer(f"{pretty(key)}: " + " ".join(parts), [self._title(key)], "pins")

    def list_devices(self) -> CannedAnswer:
        names = ", ".join(pretty(k) for k in self.gw.devices)
        return CannedAnswer(f"The {self.gw.name} documents these sensors, actuators and modules: {names}. Ask about any of them to see how to connect it.",
                            [f"{self.gw.name}: Overview"], "list")

    def gateway(self) -> CannedAnswer:
        p = self.gw.product
        return CannedAnswer(f"The {self.gw.name} is an {p.get('purpose', '').rstrip('.').replace('Educational gateway', 'educational gateway')}. "
                            "It covers: " + ", ".join(p.get("scope", [])) + ".", [f"{self.gw.name}: Overview"], "gateway")

    def interface(self, ik: str) -> CannedAnswer:
        users = self.gw.devices_on(ik)
        text = f"{pretty(ik)} on the gateway: " + "; ".join(l[2:].rstrip(".") for l in self.gw._iface_lines(ik)) + "."
        if users:
            text += " Devices that use it: " + ", ".join(pretty(u) for u in users) + "."
        return CannedAnswer(text, [f"Gateway Interface: {pretty(ik)}"], "interface")

    def rules(self) -> CannedAnswer:
        return CannedAnswer("Wiring rules:\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(self.gw.rules, 1)), ["Gateway Wiring Rules"], "rules")

    # ------------------------------------------------------------------ matching
    def match(self, message: str, mentioned: list[str], focus: list[str]) -> CannedAnswer | None:
        text = _clean(message)
        words = _WORDS.findall(text.lower())
        if not words or len(words) > 12 or "?" in text:  # a "?" left inside the text = several questions
            return None
        if _NOT_SIMPLE.search(text):
            return None
        low = text.lower()

        if not mentioned:
            if _GATEWAY.match(text):
                return self.gateway()
            if _RULES.match(text):
                return self.rules() if self.gw.rules else None
            if _LIST.search(text):
                return self.list_devices()
            if _PINS.search(low) and re.match(r"^(?:which|what)\b", low):
                keys = [ik for w, ik in _IFACE_KEYWORDS.items() if re.search(rf"\b{w}\b", low) and ik in self.gw.interfaces]
                if len(keys) == 1 and not _PRONOUN.search(low):
                    return self.interface(keys[0])

        key = self._device(low, mentioned, focus)
        if key is None:
            return None
        m = _WHAT.match(text) or _HOW_WORKS.match(text)
        if m:
            # "what is X" must be ONLY about the device: the captured subject names it (nothing else) or is a pronoun
            subject = m.group("x").lower()
            if self.gw.detect(subject) == [key] or _PRONOUN.fullmatch(subject):
                return self.what(key)
            return None
        if _PINS.search(low) and re.match(r"^(?:which|what)\b", low):
            return self.pins(key)
        if _CONNECT.search(low) and not _PINS.search(low):
            return self.connect(key)
        return None

    def _device(self, low: str, mentioned: list[str], focus: list[str]) -> str | None:
        if len(mentioned) == 1:
            return mentioned[0]
        if mentioned:
            return None  # two devices named: a comparison or a combined question
        if len(focus) == 1 and _PRONOUN.search(low):
            return focus[0]
        return None

    # ------------------------------------------------------------------ suggested questions (only ones that cost 0 tokens)
    def suggestions(self, source: str) -> list[str]:
        """Questions to suggest on a page. Each candidate is run through the real matcher, so a suggestion is offered only if clicking it
        is answered by a pre-written answer (0 tokens): the list can never drift out of sync with what is actually pre-written."""
        kind, _, key = source.partition("/")
        focus = [key] if kind == "devices" and key in self.gw.devices else []
        cands: list[str] = []
        if focus:
            name = pretty(key)
            cands = ["How do I connect this to the gateway?", "Which pins does it use?", "What is it?", f"How do I connect the {name}?"]
        elif kind == "interfaces" and key in self.gw.interfaces:
            cands = [f"Which pins are {w.upper() if w in ('i2c', 'uart') else w}?" for w, ik in _IFACE_KEYWORDS.items() if ik == key]
        cands += ["Which sensors can I connect?", "What are the wiring rules?", "Which pins support analog input?", "What is the gateway?"]
        out: list[str] = []
        for q in cands:
            a = self.match(q, self.gw.detect(q), focus)
            if a is not None and q not in out and not (a.kind == "connect" and any(o.startswith("How do I connect") for o in out)):
                out.append(q)
        return out[:4]
