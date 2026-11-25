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
_WIRING = re.compile(r"\b(?:connect\w*|wir(?:e|es|ed|ing)|plug\w*|attach\w*|hook\w*|pins?|gpio|pinout|ports?|terminals?|circuit)\b|\bwhere\b[^?.!]{0,40}\bgo(?:es)?\b", re.I)
_LINE_BY_LINE = re.compile(r"\bline[ -]?by[ -]?line\b|\b(?:explain|walk me through|go through|describe)\b[^?.!]*\b(?:the )?(?:whole |full |entire )?(?:code|program|script)\b"
                           r"|\bwhat does (?:the|this|my) (?:code|program|script) do\b", re.I)
_BLOCKS = re.compile(r"\bwhich blocks?\b|\bwhat blocks?\b|\bblocks? (?:do i|will i|should i|to|are) (?:need|use|needed|required)\b|\bblocks? (?:order|stack|sequence|needed|required)\b"
                     r"|\border of (?:the )?blocks\b|\b(?:order|stack|stacking|arrange|sequence)\b[^?.!]{0,30}\bblocks\b|\bwhich (?:block|blocks) (?:to|do)\b|\bblock programming steps\b", re.I)
_CODE = re.compile(r"\b(?:show|give|display|see|print|share|need|want|send|get)\b[^?.!]{0,40}\b(?:code|program|script)\b|^\W*(?:the )?(?:full |complete |micropython )?(?:code|program)\W*$", re.I)
_STEPS = re.compile(r"\b(?:5|five)[ -]?step|\bsteps? (?:to|of|in|for) (?:build|make|do|complete|solve|the (?:project|lesson|application|challenge))|\bhow (?:do|can|should) i (?:build|make|do|start|begin|complete) (?:this|the|a|my)?\s*(?:project|lesson|application|challenge|pulse|oximeter|health)|\bwhere (?:do|should) i start\b|\bstep[- ]by[- ]step\b|\bwhat are the steps\b", re.I)
_THRESH = re.compile(r"\bthreshold|\bnormal oxygen|\blow oxygen|\b(?:which|what) leds?\b|\bleds? (?:mean|means|show|shows|indicate|indicates|light|lights|glow)\b"
                     r"|\b(?:bpm|heart ?rate|spo2)[^?.!]{0,25}\b(?:range|band|limit|level|cut ?off|boundary|high|medium|low|normal)\b|\b(?:high|medium|low) (?:heart ?rate|bpm)\b|\b(?:95|90|80)\b", re.I)
_EXTRA = re.compile(r"\bvideo\b|\bresources?\b|\bhome ?assignment\b|\breflection\b|\bextension question|\bfood for thought\b|\bwhat should i think about\b", re.I)
_SENSOR_NAME = r"(?:pulse ?oximeter|oximeter|pulse sensor|max30102|heart rate sensor|spo2 sensor)"
_WHAT_DOES = re.compile(rf"^\W*(?:what(?:'s| is| does| do)?|how does|how do|explain|tell me about|describe)\s+(?:a |an |the |this |my )?{_SENSOR_NAME}s?(?:\s+\w+){{0,4}}\W*$", re.I)
_BUILD = re.compile(r"\b(?:how (?:do|can|should|to) (?:i |we )?(?:create|build|make|do|complete|set ?up|start)|how to (?:create|build|make|set ?up))\b[^?.!]{0,40}\b(?:this|the|a|my)?\s*(?:application|app|project|lesson|pulse|oximeter|health monitor\w*)"
                    r"|\b(?:required|needed|necessary) (?:sensors?|parts|components|hardware|materials)\b|\bwhat (?:sensors?|parts|components|hardware|materials) (?:do i|are|will i|should i)\b|\bwhat do i need\b|\b(?:parts|components|materials) (?:list|needed|required)\b"
                    r"|\bstep[- ]by[- ]step\b|\bbuild guide\b|\bcreate this (?:application|project)\b", re.I)
_FACT_Q = re.compile(r"\b(?:i2c|i²c)? ?address\b|\bfrequency\b|\bbaud(?: rate)?\b|\bwhich interface\b|\bwhat interface\b|\b(?:i2c|uart)\b[^?.!]{0,20}\b(?:or|and)\b[^?.!]{0,20}\b(?:uart|i2c)\b|\bvariable names?\b|\bdevice type\b|\bsensor type\b", re.I)
_CODE_TOKEN = re.compile(r"[a-z_][a-z0-9_]*(?:\.[a-z0-9_]+)*\([^)]*\)|\bwhile true\b|\bi2c\([^)]*\)", re.I)
_TROUBLE = re.compile(r"\bnot working\b|\bnot (?:detect|reading|respond|turn|light|show)\w*|\bdoes(?:n't| not)\b|\bwon'?t\b|\bwrong\b|\bproblem|\berror\b|\bbroken\b|\bfix\b|\bissue\b|\bwhat if\b|\bfaulty\b", re.I)
_ABOUT = re.compile(r"^\W*(?:what(?:'s| is| are| does| do)?|how does|how do|explain|tell me about|describe|about|info(?:rmation)? (?:on|about)|details? (?:of|about|on))\b", re.I)
_EXAMPLE = re.compile(r"\bexample (?:code|program)|\bsample (?:code|program)|\bpython file\b", re.I)
_LIST_Q = re.compile(r"\b(?:which|what|list|show|give me|all|how many|name)\b[^?.!]{0,30}\b(?:sensors|modules|devices|actuators|components|peripherals)\b|\bsensors? list\b", re.I)
_PINRULE_Q = re.compile(r"\b(?:which|what|list|show|tell me)\b[^?.!]{0,30}\b(?:pins?|gpio|ports?|connectors?|terminals?)\b[^?.!]{0,40}\b(?:digital|analog|i2c|uart|relay|rx|tx|sda|scl|motor|keypad)\b"
                        r"|\b(?:digital|analog|i2c|uart|relay)\b[^?.!]{0,15}\b(?:pins?|gpio)\b|\bwiring rules\b|\bpin (?:rules|connection rules)\b", re.I)
_MEASURE = re.compile(r"\bhow\b[^?.!]{0,40}\b(?:find|finds|detect|detects|count|counts|measure|measures|calculate|calculates|calculated|work out|works out|get|gets)\b[^?.!]{0,30}\b(?:heart ?beats?|beats?|bpm|heart rate|spo2|oxygen)\b|\bhow (?:does|do) the (?:block )?code (?:measure|work|read|find)\b", re.I)
_GENERIC_REF = re.compile(r"(?:^|\b(?:the|my|this|our|that|a|an|your|connect|connecting|wont|won't|not|does|do|is|are|why|how)\s+)sensor\b|\b(?:the|this|my|our) (?:sensor|circuit|project|application|app|lesson|kit|program|code|blocks?|leds?)\b|\b(?:it|this|these|them|wires?|pins?|steps?|blocks?)\b|\bshows? 0\b|\bstart\b|\bsensors? (?:are|is)\b", re.I)
_LED_Q = re.compile(r"\bled[ _]?([123])\b", re.I)
_DEF_CUE = re.compile(r"\b(?:do|does|work|works|working|is|are|mean|means|for|use|used|measure|measures)\b", re.I)
_BLOCK_Q = re.compile(r"\b(?:what|how|explain|does|do|tell|describe|purpose|used for|for)\b", re.I)

_LESSON_WORDS = re.compile(r"\bled[ _]?[123]\b|\bheart ?rate\b|\bbpm\b|\bspo2\b|\boxygen\b|\bfinger\b|\boximeter\b|\blesson\b|\bprogram\b|\bthis project\b|\bour project\b", re.I)
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

    def about_lesson(self, e: Entities) -> bool:
        """The message names the application, one of its blocks, its sensor, or a lesson word (LED, SpO2...)."""
        return bool(e.applications or e.blocks or e.weak or (set(e.sensors) & self._lesson_sensors()))

    def sensor_for_gateway_key(self, key: str) -> str | None:
        """The catalog sensor that replaces a device of the generic gateway dataset ("servo" -> servo_motor)."""
        return next((s.id for s in self.k.sensors.values() if s.gateway_key == key and s.kit_connections), None)

    def default_app(self) -> str | None:
        return next(iter(self.apps)) if len(self.apps) == 1 else None

    def app_label(self, e: Entities) -> str:
        a = (e.applications or e.weak or [self.default_app()])[0]
        return self.apps[a].title if a else ""

    # ------------------------------------------------------------------ routing helpers
    def claims(self, message: str, e: Entities, recent: Entities | None = None) -> bool:
        """A message that names a generic gateway device ("LED") but is clearly about the lesson ("LED 2", "heart rate", "the program"),
        or follows a conversation about the lesson ("what about the LED?")."""
        return bool(e.weak and (_LESSON_WORDS.search(message) or (recent is not None and recent.strong)))

    def is_general(self, message: str, e: Entities, hw_named: bool) -> bool:
        """A "what is X / how does X work" question about a technology: answered from model knowledge in student language."""
        if e.strong or not (GENERAL_CUE.search(message) and GENERAL_TOPIC.search(message)):
            return False
        # a comparison of two ideas ("difference between a sensor and an LED") is general knowledge even when a gateway device is named
        return not hw_named or bool(re.search(r"\bdifference between\b|\bversus\b|\bvs\.?\b", message, re.I))

    # ------------------------------------------------------------------ answers
    def answer(self, message: str, e: Entities, recent: Entities, hw_named: bool = False, page_only: bool = False) -> AppAnswer | None:
        """A deterministic answer, or None to fall through to retrieval / the model."""
        if not (hw_named and not page_only and not e.strong):  # a gateway device NAMED in the message (not just an open page) keeps the old flow
            cat = self._catalog_answer(message, e)
            if cat:
                return cat
        if (e.sensors and all(self.k.sensors[x].kit_connections and x not in self._lesson_sensors() for x in e.sensors)
                and not e.applications and not e.blocks):
            return None  # about another sensor of the list (e.g. "what if the ultrasonic sensor is not working"): not the lesson, so retrieval answers
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
        from_default = not (e.applications or e.sensors or e.blocks or e.kits or e.weak or recent.strong)  # nothing in the message points at the lesson
        if hw_named and not (e.applications or e.sensors or e.blocks):
            # a generic gateway word ("led") plus an application hint ("spo2", "LED 3 OFF"): only the exact FAQ / limits are safe to answer
            return (self._faq(a, msg) if e.weak else None) or (self._thresholds(a) if (e.weak and _THRESH.search(msg)) else None)

        if "govator_controller_pi" in e.kits and re.search(r"\bled\b|led board", msg, re.I) and _WIRING.search(msg):
            return self._controller_gap(a)  # the lesson's LED board on the controller is not documented

        if _LINE_BY_LINE.search(msg):
            return self._line_by_line(a)
        if _BUILD.search(msg) and not _CODE.search(msg):
            return self._build_guide(a)
        if _MEASURE.search(msg):
            how = self.k.sensors[a.circuit.sensor].how_it_measures
            if how:
                return AppAnswer("how_it_measures", "How the block code measures:\n" + "\n".join(f"- {h}" for h in how), [f"sensors/{a.circuit.sensor}/measure"])
        stmt = self._statement(a, msg)
        if stmt:
            return stmt
        fact = self._sensor_fact(a, e, msg)
        if fact:
            return fact
        block = self._block_named(e)
        if block and _BLOCK_Q.search(msg) and not _CODE.search(msg):
            return self._block_detail(block)
        if _CODE.search(msg) and not _BLOCKS.search(msg):
            return self._code(a, e, msg, recent)
        if _BLOCKS.search(msg):
            return self._block_stack(a)
        led = _LED_Q.search(msg)
        if led and (_WIRING.search(msg) or re.search(r"\b(?:gpio|pin|port)\b", msg, re.I)):
            return self._led_pin(a, int(led.group(1)))
        if _WIRING.search(msg) and not _STEPS.search(msg) and (not from_default or _GENERIC_REF.search(msg)):
            return self._wiring(a, e)  # ("how do I connect the MPU6050" must not be answered with the lesson's wiring)
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

    def _build_guide(self, a) -> AppAnswer:
        """Required parts + connections + numbered instructions, assembled from the application file."""
        k = self.k
        parts = [(k.sensors.get(c) or k.kits.get(c)).name if not c.startswith("other:") else c[6:] for c in a.components]
        steps = a.build_steps or [f"{s.title}: {s.text}" for s in a.steps]
        text = ("To create this application you need:\n" + "\n".join(f"- {p}" for p in parts)
                + "\n\nConnections (ESP32 kit): see the table below.\n\nStep by step:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(steps, 1)))
        rows = [{"sensor_pin": c.sensor_pin, "kit_pin": c.kit_pin, "note": c.note, "verified": c.verified} for c in a.circuit.connections]
        bl = [k.blocks[b] for b in a.block_stack]
        return AppAnswer("build_guide", text, [f"applications/{a.id}/steps", f"applications/{a.id}/circuit", *[f"blocks/{b.id}" for b in bl]],
                         {"title": "Pulse oximeter to ESP32 kit", "columns": ["Sensor pin", "Kit pin", "Note"], "rows": rows, "note": "From the lesson's circuit diagram"},
                         blocks=[{"order": i, "title": b.title, "description": b.description} for i, b in enumerate(bl, 1)],
                         images=self._img(a.circuit.image))

    # ---- sensor catalog (sensor list + gateway dataset) -------------------------------------------------
    def _kit_short(self, kit_id: str) -> str:
        return self.k.kits[kit_id].name.split(" (")[0]

    @staticmethod
    def _cat(s) -> str:
        return s.category.lower() if s.category not in ("", "-") else "sensor"

    def _lesson_sensors(self) -> set[str]:
        return {a.circuit.sensor for a in self.apps.values()}

    def _catalog_answer(self, message: str, e: Entities) -> AppAnswer | None:
        msg = message
        if _LED_Q.search(msg):
            return None  # "LED 1 / led_2" are the lesson's LEDs
        sensors = [x for x in e.sensors if self.k.sensors[x].kit_connections]
        if sensors == ["relay_module"] and _PINRULE_Q.search(msg):
            return self._pin_rules(msg, e.kits)  # "which pins are the relay pins" is about the kit, not the relay module
        if sensors:
            sid = sensors[0]
            s = self.k.sensors[sid]
            esp_only = e.kits == ["esp32_kit"]
            if sid in self._lesson_sensors() and (not e.kits or esp_only):
                return None  # the lesson's own wiring (verified diagram) is answered by the application
            wiring = _WIRING.search(msg)
            if wiring and not _STEPS.search(msg) and not _CODE.search(msg):
                return self._sensor_connect(s, e.kits)
            if _EXAMPLE.search(msg) or (_CODE.search(msg) and s.example_code and sid not in self._lesson_sensors()):
                return self._sensor_example(s)
            if _ABOUT.search(msg) and not _TROUBLE.search(msg):
                return self._sensor_about(s, e.kits)
            return None
        if _PINRULE_Q.search(msg):
            return self._pin_rules(msg, e.kits)
        if _LIST_Q.search(msg) and not e.strong and not _BUILD.search(msg):
            return self._sensor_list(msg)
        return None

    def _digital_hint(self, s, kit_id: str) -> str:
        rule = next((r for r in self.k.kits[kit_id].pin_rules if r.name == {"digital": "digital", "analog": "analog"}.get(s.interface, "")), None)
        return f" ({rule.name} pins: {', '.join(rule.pins)})" if rule and rule.name in ("digital", "analog") else ""

    def _sensor_connect(self, s, kit_ids: list[str]) -> AppAnswer:
        shown = [kc for kc in s.kit_connections if not kit_ids or kc.kit in kit_ids]
        src = [f"{s.name} > Sensor details"]
        if not shown:
            return AppAnswer("not_documented", f"I don't have that detail yet: the {s.name} has no documented connection for "
                             + " or ".join(self._kit_short(x) for x in kit_ids) + ". Please check your kit documentation or ask your teacher.", src, grounded=True)
        lines, rows = [], []
        for kc in shown:
            kit = self._kit_short(kc.kit)
            pins = "; ".join(f"{p.pin} to {p.connects_to}" for p in kc.pins) or "no pins are given"
            lines.append(f"{kit}{f' ({kc.interface})' if kc.interface else ''}: {pins}." + self._digital_hint(s, kc.kit) + (f" {kc.note}." if kc.note else "") + (f" Warning: {kc.warning}" if kc.warning else ""))
            rows += [{"sensor_pin": p.pin, "kit_pin": p.connects_to, "note": kit, "verified": True} for p in kc.pins]
        tail = ""
        if not kit_ids:
            missing = [x for x in ("govator_controller_pi", "esp32_kit") if x not in {kc.kit for kc in shown}]
            tail = (" I showed every kit that documents it; tell me which kit you use." if len(shown) > 1 else
                    f" For the {' and '.join(self._kit_short(x) for x in missing)} this sensor's connection is not documented in my files.")
        text = f"{s.name} ({self._cat(s)}, {s.interface}) wiring:\n" + "\n".join(lines) + tail
        return AppAnswer("sensor_connect", text, src, {"title": f"{s.name}: connections", "columns": ["Sensor pin", "Connects to", "Kit"], "rows": rows,
                                                       "note": "From the sensor list and the gateway dataset"})

    def _sensor_about(self, s, kit_ids: list[str]) -> AppAnswer:
        text = f"{s.name} ({self._cat(s)}, {s.interface}): {s.working or s.description}"
        if s.kit_connections:
            text += "\n\nHow it connects: " + " ".join(
                f"{self._kit_short(kc.kit)}: " + "; ".join(f"{p.pin} to {p.connects_to}" for p in kc.pins) + "." for kc in s.kit_connections if not kit_ids or kc.kit in kit_ids)
        return AppAnswer("sensor_about", text, [f"{s.name} > Sensor details"])

    def _sensor_example(self, s) -> AppAnswer:
        files = [(self._kit_short(kc.kit), kc.example_code) for kc in s.kit_connections if kc.example_code] or ([("sensor list", s.example_code)] if s.example_code else [])
        text = (f"The sensor lists name these example programs for the {s.name}: " + "; ".join(f"{k}: {f}" for k, f in files)
                + ". I don't have their code, so I can't show it." if files else f"The sensor lists give no example program for the {s.name}. I don't have that detail yet.")
        return AppAnswer("sensor_example", text, [f"{s.name} > Sensor details"])

    def _pin_rules(self, msg: str, kit_ids: list[str]) -> AppAnswer:
        wanted = [w for w in ("digital", "analog", "i2c", "uart", "relay", "keypad", "motor", "stepper") if re.search(rf"\b{w}\b", msg, re.I)]
        if re.search(r"\b(?:sda|scl)\b", msg, re.I) and "i2c" not in wanted:
            wanted.append("i2c")
        if re.search(r"\b(?:rx|tx)\b", msg, re.I) and "uart" not in wanted:
            wanted.append("uart")
        kits = [self.k.kits[x] for x in (kit_ids or list(self.k.kits)) if x in self.k.kits and self.k.kits[x].pin_rules]
        lines, src = [], []
        for kit in kits:
            rules = [r for r in kit.pin_rules if not wanted or any(w in r.name for w in wanted)]
            if re.search(r"\bwiring rules\b", msg, re.I) and kit.wiring_rules:
                lines.append(f"{self._kit_short(kit.id)} wiring rules:\n" + "\n".join(f"- {r}" for r in kit.wiring_rules))
            for r in rules:
                lines.append(f"{self._kit_short(kit.id)} {r.name}: {', '.join(r.pins)}" + (f" ({r.note})" if r.note else ""))
            src.append(f"Kit: {kit.name}")
        if not lines:
            return AppAnswer("not_documented", NOT_DOCUMENTED, src or ["kits"])
        return AppAnswer("pin_rules", "\n".join(lines) + ("\n\nTell me which kit you use if you want only one." if len(kits) > 1 and not kit_ids else ""), src)

    def _sensor_list(self, msg: str) -> AppAnswer:
        iface_words = {"i2c": "I2C", "uart": "UART", "analog": "analog", "digital": "digital", "usb": "USB", "relay": "relay"}
        want_if = [v for w, v in iface_words.items() if re.search(rf"\b{w}\b", msg, re.I)]
        want_cat = "Output" if re.search(r"\bactuators?|\boutputs?\b", msg, re.I) else ("Input" if re.search(r"\binput (?:sensors|devices)\b|\binputs\b", msg, re.I) else "")
        items = [s for s in self.k.sensors.values() if s.kit_connections or s.working]
        if want_if:
            items = [s for s in items if s.interface in want_if]
        if want_cat:
            items = [s for s in items if s.category == want_cat or not s.category]
        groups: dict[str, list[str]] = {}
        for s in items:
            groups.setdefault(f"{s.category if s.category not in ('', '-') else 'Other'} - {s.interface}", []).append(s.name)
        text = f"{len(items)} documented:\n" + "\n".join(f"{g}: {', '.join(sorted(n))}" for g, n in sorted(groups.items()))
        return AppAnswer("sensor_list", text + "\n\nAsk me how any of them works or connects.", ["Sensor list"])

    def _statement(self, a, msg: str) -> AppAnswer | None:
        """"What does led_1.on() do?": find the line-by-line part of the lesson program that contains that statement."""
        toks = [re.sub(r"\s+", "", t.lower()) for t in _CODE_TOKEN.findall(msg.replace("`", ""))]
        toks = [t for t in toks if len(t) >= 6]
        if not toks:
            return None
        hits = [(i, ex) for i, ex in enumerate(a.program.explanations, 1) for tok in toks
                if any(tok in re.sub(r"\s+", "", frag.lower()) for frag in ex.code.split(" / "))]
        if not hits:
            return None
        i, ex = hits[0]
        extra = f" (This statement appears in {len(hits)} places in the program; this is the first.)" if len(hits) > 1 else ""
        text = f"That is part of this section of the lesson program:\n{ex.code.replace(' / ', chr(10))}\n\nWhat it does: " + " ".join(ex.meaning) + extra
        return AppAnswer("statement", text, [f"applications/{a.id}/line-by-line-{i}"])

    def _sensor_fact(self, a, e: Entities, msg: str) -> AppAnswer | None:
        """Plain facts of the sensor file (I2C address, frequency, UART baud rate, variable names): read from it, not searched for."""
        sid = e.sensors[0] if e.sensors else (a.circuit.sensor if (e.any or not e.kits) else None)
        s = self.k.sensors.get(sid or "")
        if not s or not _FACT_Q.search(msg) or not (e.sensors or re.search(r"\b(?:sensor|oximeter|max30102|i2c|uart)\b", msg, re.I)):
            return None
        m, src = msg.lower(), [f"sensors/{s.id}"]
        if s.i2c and re.search(r"address", m):
            return AppAnswer("fact", f"The {s.name.split(' (')[0]} uses I2C address {s.i2c.address} (SCL {s.i2c.scl}, SDA {s.i2c.sda}).", src)
        if s.i2c and re.search(r"frequency", m) and s.i2c.freq:
            return AppAnswer("fact", f"The I2C frequency in the block code is {s.i2c.freq} Hz.", src)
        if s.uart and re.search(r"baud", m):
            return AppAnswer("fact", f"The {s.name} uses UART {s.uart.uart_id} at {s.uart.baudrate} baud (TX GPIO{s.uart.tx_gpio}, RX GPIO{s.uart.rx_gpio} in the code).", src)
        if re.search(r"variable", m):
            return AppAnswer("fact", f"The variable names for the {s.name.split(' (')[0]} are: " + ", ".join(s.variables) + ".", src)
        if re.search(r"interface|i2c|uart", m):
            return AppAnswer("fact", f"The {s.name.split(' (')[0]} uses {s.interface}." + (" Not UART/RX/TX: the lesson's circuit uses SDA and SCL." if s.interface == "I2C" else ""), src)
        return None

    def _led_pin(self, a, n: int) -> AppAnswer | None:
        led = next((l for l in a.circuit.leds if l.name == f"led_{n}"), None)
        if led is None:
            return None
        text = (f"In the lesson program LED {n} ({led.name}) is set up as an output on GPIO{led.gpio}. It means: {led.meaning}. Which terminal of the LED board "
                f"its wire uses is not readable in the lesson's diagram, so I don't have that detail yet: ask your teacher.")
        return AppAnswer("led_pin", text, [f"applications/{a.id}/circuit"], images=self._img(a.circuit.image))

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

    def _code(self, a, e: Entities, msg: str, recent: Entities | None = None) -> AppAnswer:
        asks_program = re.search(r"\b(?:lesson|program|full|whole|complete|micropython)\b", msg, re.I)
        wants_block = bool(e.blocks) or re.search(r"\bblocks?\b", msg, re.I) or bool(recent and recent.blocks and not asks_program)
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
