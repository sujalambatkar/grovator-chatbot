"""Application assistant: entity detection + deterministic, structured answers from the file-based knowledge base.

Anything that must be exactly right (wiring tables, block order, code, thresholds, "not documented yet") is built from the YAML
here, in code: 0 LLM tokens and nothing the model could get wrong. Everything else falls through to retrieval (grounded
answers) or to the general-knowledge path.
"""
import difflib
import re
from dataclasses import dataclass, field

from knowledge.loader import Knowledge

HEALTH_NOTE = ("Remember: this is a learning project, not a medical device. For real health concerns, "
               "talk to a doctor or a trusted adult.")
NOT_DOCUMENTED = "I don't have that detail yet. Please check your kit documentation or ask your teacher."
_HEALTH = re.compile(r"\b(?:spo2|oxygen|heart ?rate|heartrate|bpm|pulse rate|readings?|health|healthy|diagnos\w*|medical|doctor|covid|accurate|accuracy|normal)\b", re.I)
_MEDIA_URL = "/media/"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", s.lower()).strip()


def needs_health_note(message: str, answer: str) -> bool:
    return bool(_HEALTH.search(message)) and "learning project" not in answer.lower()


def with_health_note(message: str, answer: str) -> str:
    return f"{answer}\n\n{HEALTH_NOTE}" if needs_health_note(message, answer) else answer


@dataclass
class Entities:
    applications: list[str] = field(default_factory=list)
    sensors: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    kits: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)   # applications hinted only by weak words (led, spo2, ...)

    @property
    def strong(self) -> bool:
        return bool(self.applications or self.sensors or self.blocks or self.kits)

    @property
    def any(self) -> bool:
        return self.strong or bool(self.weak)

    def merge(self, other: "Entities") -> "Entities":
        return Entities(*[list(dict.fromkeys(a + b)) for a, b in zip(
            (self.applications, self.sensors, self.blocks, self.kits, self.weak),
            (other.applications, other.sensors, other.blocks, other.kits, other.weak))])


@dataclass
class AppAnswer:
    kind: str
    text: str
    sources: list[str]
    table: dict | None = None            # {"title", "columns", "rows", "note"}
    blocks: list[dict] = field(default_factory=list)   # [{"order", "title", "description"}]
    code: list[dict] = field(default_factory=list)     # [{"title", "language", "code", "note"}]
    images: list[dict] = field(default_factory=list)   # [{"url", "caption", "verified"}]
    grounded: bool = True


# ------------------------------------------------------------------ question shapes (strict; a wrong template is worse than a model call)
_WIRING = re.compile(r"\b(?:connect\w*|wir(?:e|es|ed|ing)|plug\w*|attach\w*|hook\w*|pins?|pinout|ports?|terminals?|circuit)\b", re.I)
_LINE_BY_LINE = re.compile(r"\bline[ -]?by[ -]?line\b|\b(?:explain|walk me through|go through|describe)\b[^?.!]*\b(?:the )?(?:whole |full |entire )?(?:code|program|script)\b"
                           r"|\bwhat does (?:the|this|my) (?:code|program|script) do\b", re.I)
_BLOCKS = re.compile(r"\bwhich blocks?\b|\bwhat blocks?\b|\bblocks? (?:do i|will i|should i|to|are) (?:need|use|needed|required)\b|\bblocks? (?:order|stack|sequence|needed|required)\b"
                     r"|\border of (?:the )?blocks\b|\bwhich (?:block|blocks) (?:to|do)\b|\bblock programming steps\b", re.I)
_CODE = re.compile(r"\b(?:show|give|display|see|print|share|need|want|send|get)\b[^?.!]{0,40}\b(?:code|program|script)\b|^\W*(?:the )?(?:full |complete |micropython )?(?:code|program)\W*$", re.I)
_STEPS = re.compile(r"\b(?:5|five)[ -]?step|\bsteps? (?:to|of|in|for) (?:build|make|do|complete|solve|the (?:project|lesson|application|challenge))|\bhow (?:do|can|should) i (?:build|make|do|start|begin|complete) (?:this|the|a|my)?\s*(?:project|lesson|application|challenge|pulse|oximeter|health)|\bwhere (?:do|should) i start\b|\bstep[- ]by[- ]step\b|\bwhat are the steps\b", re.I)
_THRESH = re.compile(r"\bthreshold|\bnormal oxygen|\blow oxygen|\b(?:which|what) leds?\b|\bleds? (?:mean|means|show|shows|indicate|indicates|light|lights|glow)\b"
                     r"|\b(?:bpm|heart ?rate|spo2)[^?.!]{0,25}\b(?:range|band|limit|level|cut ?off|boundary|high|medium|low|normal)\b|\b(?:high|medium|low) (?:heart ?rate|bpm)\b|\b(?:95|90|80)\b", re.I)
_EXTRA = re.compile(r"\bvideo\b|\bresources?\b|\bhome ?assignment\b|\breflection\b|\bextension question|\bfood for thought\b|\bwhat should i think about\b", re.I)
_WHAT_DOES = re.compile(r"^\W*(?:what(?:'s| is| does| do)?|how does|how do|explain|tell me about|describe)\b[^?]*\b(?:pulse ?oximeter|oximeter|pulse sensor|max30102|heart rate sensor|spo2 sensor)\b", re.I)
_DEF_CUE = re.compile(r"\b(?:do|does|work|works|working|is|are|mean|means|for|use|used|measure|measures)\b", re.I)
_BLOCK_Q = re.compile(r"\b(?:what|how|explain|does|do|tell|describe|purpose|used for|for)\b", re.I)

GENERAL_CUE = re.compile(r"^\W*(?:what is|what are|what's|whats|what does [^?]{1,40} mean|define|explain|how does [^?]{1,50} work|how do [^?]{1,50} work|why (?:do|does|is|are)|"
                         r"difference between|meaning of|tell me about|what do you mean by|can you explain)\b", re.I)
GENERAL_TOPIC = re.compile(r"\b(?:spo2|sp02|oxygen saturation|blood oxygen|oxygen|bpm|heart ?rate|heartbeat|pulse|i2c|i²c|uart|serial|gpio|led|leds|relay|micropython|python|iot|aiot|iiot|"
                           r"internet of things|gateway|cloud|sensor|sensors|esp32|block coding|block programming|variable|variables|loop|if else|voltage|current|resistor|sda|scl|"
                           r"infrared|photodetector|light|reflection|refraction|signal|amplifier|baud|sleep|library|import|print|function|conditions?)\b", re.I)


class AppKB:
    def __init__(self, k: Knowledge) -> None:
        self.k = k
        self.apps = k.applications
        self._strong: list[tuple[re.Pattern, str, str]] = []
        self._weak: list[tuple[re.Pattern, str]] = []
        self._single_words: dict[str, tuple[str, str]] = {}
        self._build()

    # ------------------------------------------------------------------ detection
    def _add(self, alias: str, kind: str, ident: str) -> None:
        a = norm(alias)
        if len(a) >= 3:
            self._strong.append((re.compile(rf"(?<![a-z0-9]){re.escape(a)}s?(?![a-z0-9])"), kind, ident))
            if " " not in a and len(a) >= 6:
                self._single_words[a] = (kind, ident)

    def _build(self) -> None:
        for a in self.apps.values():
            for al in [a.title, *a.aliases]:
                self._add(al, "application", a.id)
            self._weak += [(re.compile(rf"(?<![a-z0-9]){re.escape(norm(w))}s?(?![a-z0-9])"), a.id) for w in a.weak_aliases]
        for s in self.k.sensors.values():
            for al in [s.name, *s.aliases]:
                self._add(al, "sensor", s.id)
        for b in self.k.blocks.values():
            for al in [b.title, *b.aliases]:
                self._add(al, "block", b.id)
        for kit in self.k.kits.values():
            for al in [kit.name, *kit.aliases]:
                self._add(al, "kit", kit.id)
        self._strong.sort(key=lambda t: -len(t[0].pattern))

    def detect(self, text: str) -> Entities:
        t = norm(text)
        e, taken = Entities(), []
        for pat, kind, ident in self._strong:
            m = pat.search(t)
            if m and not any(s < m.end() and m.start() < en for s, en in taken):
                taken.append((m.start(), m.end()))
                getattr(e, kind + "s").append(ident)
        for tok in re.findall(r"[a-z0-9.]{6,}", t):  # typos: "oxymeter", "pulseoximter"
            if tok in self._single_words or any(tok.startswith(w) for w in self._single_words):
                continue
            close = difflib.get_close_matches(tok, list(self._single_words), n=1, cutoff=0.84)
            if close:
                kind, ident = self._single_words[close[0]]
                if ident not in getattr(e, kind + "s"):
                    getattr(e, kind + "s").append(ident)
        for pat, app_id in self._weak:
            if pat.search(t) and app_id not in e.weak:
                e.weak.append(app_id)
        return e

    def default_app(self) -> str | None:
        return next(iter(self.apps)) if len(self.apps) == 1 else None

    def app_label(self, e: Entities) -> str:
        a = (e.applications or e.weak or [self.default_app()])[0]
        return self.apps[a].title if a else ""

    # ------------------------------------------------------------------ routing helpers
    def is_general(self, message: str, e: Entities, hw_named: bool) -> bool:
        """A "what is X / how does X work" question about a technology: answered from model knowledge in student language."""
        return bool(not hw_named and not e.strong and GENERAL_CUE.search(message) and GENERAL_TOPIC.search(message))

    # ------------------------------------------------------------------ answers
    def answer(self, message: str, e: Entities, recent: Entities, hw_named: bool = False) -> AppAnswer | None:
        """A deterministic answer, or None to fall through to retrieval / the model."""
        ctx = e.merge(recent) if (e.strong or recent.strong) else e
        app_id = (ctx.applications or ctx.weak or [None])[0]
        if app_id is None and ctx.sensors:
            app_id = next((a.id for a in self.apps.values() if a.circuit.sensor in ctx.sensors), None)
        if app_id is None and not hw_named and self.default_app() and not (e.kits and not e.sensors):
            app_id = self.default_app()  # one application installed: "How do I connect the sensor?" is about it
        if app_id is None:
            return None
        a = self.apps[app_id]
        msg = message
        if hw_named and not e.strong:
            # a generic gateway word ("led") plus an application hint ("spo2", "LED 3 OFF"): only the exact FAQ / limits are safe to answer
            return (self._faq(a, msg) if e.weak else None) or (self._thresholds(a) if (e.weak and _THRESH.search(msg)) else None)

        if "govator_controller_pi" in e.kits or ("govator_controller_pi" in ctx.kits and _WIRING.search(msg) and not e.kits):
            if _WIRING.search(msg) or re.search(r"\b(?:use|work|used|support|compatible)\b", msg, re.I):
                if e.sensors or e.applications or recent.strong or re.search(r"\b(?:oximeter|sensor)\b", msg, re.I):
                    return self._controller_gap(a)
            return None

        if _LINE_BY_LINE.search(msg):
            return self._line_by_line(a)
        block = self._block_named(e)
        if block and _BLOCK_Q.search(msg) and not _CODE.search(msg):
            return self._block_detail(block)
        if _CODE.search(msg) and not _BLOCKS.search(msg):
            return self._code(a, e, msg)
        if _BLOCKS.search(msg):
            return self._block_stack(a)
        if _WIRING.search(msg) and not _STEPS.search(msg):
            return self._wiring(a, e)
        if _STEPS.search(msg):
            return self._steps(a)
        if _EXTRA.search(msg):
            return self._extra(a)
        faq = self._faq(a, msg)
        if faq:
            return faq
        if _THRESH.search(msg) and (ctx.any or e.any):
            return self._thresholds(a)
        if _WHAT_DOES.search(msg) and _DEF_CUE.search(msg):
            return self._what_is(a)
        return None

    # ---- builders -------------------------------------------------------------------------------
    def _img(self, media_id: str) -> list[dict]:
        m = self.k.media.get(media_id)
        return [{"url": _MEDIA_URL + m.file, "caption": m.caption, "verified": m.verified}] if m else []

    def _wiring(self, a, e: Entities) -> AppAnswer:
        base = f"applications/{a.id}"
        if "pulseoximeter_v2_uart" in e.sensors and "pulseoximeter_max30102" not in e.sensors:
            s = self.k.sensors["pulseoximeter_v2_uart"]
            rows = [{"sensor_pin": p.pin, "kit_pin": p.connects_to, "note": "", "verified": False} for p in s.pins]
            text = ("The V2.0 pulse oximeter is a different version from the one in the lesson: it talks over UART, not I2C. "
                    "The document gives these connections: 3V3 to 3.3V, GND to GND, RX to TXD, TX to RXD (TX and RX are crossed). "
                    "Which terminals on the kit carry TXD and RXD is not documented yet, and the table is not confirmed, so please check with your teacher.")
            return AppAnswer("wiring", text, [f"sensors/{s.id}"], {"title": f"{s.name}: connections", "columns": ["Sensor pin", "Connects to", "Note"],
                             "rows": rows, "note": "Not confirmed: see GAPS.md"}, grounded=True)
        sensor = self.k.sensors[a.circuit.sensor]
        kit = self.k.kits[a.circuit.kit]
        rows = [{"sensor_pin": c.sensor_pin, "kit_pin": c.kit_pin, "note": c.note, "verified": c.verified} for c in a.circuit.connections]
        pins = ", ".join(f"{c.sensor_pin} to {c.kit_pin}" for c in a.circuit.connections)
        leds = ", ".join(f"{l.name} = GPIO{l.gpio}" for l in a.circuit.leds)
        text = (f"The {sensor.name.split(' (')[0]} connects to the {kit.name.split(' (')[0]} with 4 wires (it uses {sensor.interface}): {pins}. "
                f"The program sets the LEDs on {leds}. Which terminals of the LED board each wire uses is not readable in the lesson's "
                f"diagram, so I don't have that detail yet: ask your teacher. "
                f"This is the wiring for the ESP32 kit in the lesson. If you are using the Grovator controller instead, tell me: that connection is not documented yet.")
        return AppAnswer("wiring", text, [f"{base}/circuit"], {"title": "Pulse oximeter to ESP32 kit", "columns": ["Sensor pin", "Kit pin", "Note"],
                         "rows": rows, "note": "From the lesson's circuit diagram"}, images=self._img(a.circuit.image))

    def _controller_gap(self, a) -> AppAnswer:
        text = ("I don't have that detail yet. The documents I have don't say how the pulse oximeter connects to the Grovator controller. "
                "Its GPIO numbers are different from the ESP32 kit in the lesson (for example GPIO22 is Relay 2 on the controller, but it is the I2C clock, SCL, on the ESP32 kit), "
                "so I can't copy the lesson's wiring across. Please check your kit documentation or ask your teacher.")
        return AppAnswer("not_documented", text, ["kits/govator_controller_pi"], grounded=True)

    def _block_stack(self, a) -> AppAnswer:
        bl = [self.k.blocks[b] for b in a.block_stack]
        chain = " -> ".join(f"[{b.title}]" for b in bl)
        text = (f"You need 3 blocks, stacked top to bottom: {chain}. 'Use' sets up the sensor, 'Read' loads the sensor functions, and 'Store' puts the heart rate and "
                f"SpO2 into two variables you can use. Ask me to show the code of any block. The lesson's own program also uses blocks for the LED board, "
                f"time, print and if/else; I don't have their block definitions yet.")
        return AppAnswer("blocks", text, [f"blocks/{b.id}" for b in bl],
                         blocks=[{"order": i, "title": b.title, "description": b.description} for i, b in enumerate(bl, 1)], images=self._img("deck_page_11"))

    def _block_named(self, e: Entities):
        return self.k.blocks[e.blocks[0]] if e.blocks else None

    def _block_detail(self, b) -> AppAnswer:
        text = f"{b.title}: {b.description}"
        return AppAnswer("block", text, [f"blocks/{b.id}"], blocks=[{"order": 1, "title": b.title, "description": b.description}])

    def _code(self, a, e: Entities, msg: str) -> AppAnswer:
        wants_block = bool(e.blocks) or re.search(r"\bblocks?\b", msg, re.I)
        if wants_block:
            bl = [self.k.blocks[i] for i in (e.blocks or a.block_stack)]
            code = [{"title": b.title, "language": b.language, "code": b.code.rstrip(), "note": " ".join(b.code_notes)} for b in bl]
            text = "Here is the code behind " + ("this block." if len(bl) == 1 else "the 3 blocks, in the order you stack them.")
            return AppAnswer("code", text, [f"blocks/{b.id}" for b in bl], code=code)
        text = (f"Here is the lesson's program (MicroPython). It reads the heart rate and SpO2 forever, prints them, and lights LED 1, 2 or 3 by heart rate. "
                f"Ask me to explain it line by line.")
        return AppAnswer("code", text, [f"applications/{a.id}/program"],
                         code=[{"title": a.title + ": program", "language": "python", "code": a.program.code.rstrip(), "note": " ".join(a.program.code_notes)}])

    def _line_by_line(self, a) -> AppAnswer:
        parts = [f"{i}. {e.code}\n   " + " ".join(e.meaning) for i, e in enumerate(a.program.explanations, 1)]
        text = "Here is what the lesson's program does, part by part:\n\n" + "\n\n".join(parts)
        return AppAnswer("explain_code", text, [f"applications/{a.id}/line-by-line-{i}" for i in range(1, len(a.program.explanations) + 1)])

    def _steps(self, a) -> AppAnswer:
        text = "The lesson uses a 5 step process:\n" + "\n".join(f"{s.n}. {s.title}: {s.text}" for s in a.steps)
        return AppAnswer("steps", text, [f"applications/{a.id}/steps"], images=self._img("deck_page_6"))

    def _thresholds(self, a) -> AppAnswer:
        rows = [{"sensor_pin": t.name, "kit_pin": t.rule, "note": t.result, "verified": True} for t in a.thresholds]
        text = ("These are the limits used in the lesson's program. SpO2: 95 or more prints 'Normal oxygen level', below 95 prints 'Low oxygen level'. "
                "Heart rate: 90 BPM or more lights LED 1, 80 to 89 lights LED 2, below 80 lights LED 3 (the LEDs follow the heart rate, not the oxygen level). "
                "A reading of 0 means no finger or no valid reading. These are classroom limits, not medical advice.")
        return AppAnswer("thresholds", text, [f"applications/{a.id}/logic"], {"title": "Limits in the lesson program", "columns": ["Case", "Rule", "What happens"],
                         "rows": rows, "note": "Classroom limits, not medical advice"})

    def _extra(self, a) -> AppAnswer:
        text = "Resources:\n" + "\n".join(f"- {r}" for r in a.resources) + "\n\nThink about it:\n" + "\n".join(f"- {q}" for q in a.extension_questions)
        return AppAnswer("extension", text, [f"applications/{a.id}/extension"])

    def _what_is(self, a) -> AppAnswer:
        s = self.k.sensors[a.circuit.sensor]
        text = (f"A pulse oximeter measures your heart rate (BPM) and the oxygen level in your blood (SpO2) from your fingertip. {a.science[1]} {a.science[2]} "
                f"The sensor turns that change into an electrical signal for the kit to read. {a.science[0]}")
        return AppAnswer("what_is", text, [f"sensors/{s.id}", f"applications/{a.id}/docs/lesson.md#4"], images=self._img("deck_page_18"))

    def _faq(self, a, msg: str) -> AppAnswer | None:
        t = " " + norm(msg) + " "
        best, score = None, 0
        for i, f in enumerate(a.faq, 1):
            for trig in [norm(x) for x in f.triggers] + [norm(f.q)]:
                if trig and f" {trig} " in t and len(trig) > score:
                    best, score = (i, f), len(trig)
        if not best:
            return None
        i, f = best
        return AppAnswer("faq", f.a, [f"applications/{a.id}/faq-{i}"])

    def images_for(self, sources: list[str]) -> list[dict]:
        """One related image for retrieved sections (the circuit diagram for wiring, the block program for code, ...)."""
        for src in sources:
            for a in self.apps.values():
                if src == f"applications/{a.id}/circuit":
                    return self._img(a.circuit.image)
                if src.startswith(f"applications/{a.id}/program") or "/line-by-line" in src or src.startswith("blocks/"):
                    return self._img("deck_page_11")
                if src.endswith("/steps") or src.endswith("/overview"):
                    return self._img("deck_page_6")
            if src.startswith("sensors/") and src.endswith("/measure"):
                return self._img("deck_page_18")
        return []
