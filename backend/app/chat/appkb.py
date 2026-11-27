"""Application assistant: entity detection + deterministic, structured answers from the file-based knowledge base.

Anything that must be exactly right (wiring tables, block order, code, thresholds, "not documented yet") is built from the YAML
here, in code: 0 LLM tokens and nothing the model could get wrong. Everything else falls through to retrieval (grounded
answers) or to the general-knowledge path.
"""
import difflib
import re
from dataclasses import dataclass, field

from app.guardrails.hazard import detect_hazard
from knowledge.loader import Knowledge

HEALTH_NOTE = ("Remember: this is a learning project, not a medical device. For real health concerns, "
               "talk to a doctor or a trusted adult.")
PALETTE_NOTE = ""    # the "purposes are inferred" caveat lives in knowledge/GAPS.md; students do not see it
PALETTE_FOOTER = ""
NOT_DOCUMENTED = "I don't have that detail yet. Please check your kit documentation or ask your teacher."
_HEALTH = re.compile(r"\b(?:spo2|oxygen|heart ?rate|heartrate|bpm|pulse(?: rate)?|oximeter|ecg|blood pressure|health|healthy|diagnos\w*|medical|doctor|covid)\b", re.I)
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
    footer: str = ""                     # small print shown under the structured parts (block list, table...)
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
_SPEC_Q = re.compile(r"\b(?:the )?(?:weight|price|cost|size|dimensions?)\s+(?:of|for)\b|\bhow much (?:does|is|do)\b|\b(?:price|cost|warranty)\b|\bwhere (?:to|can i) buy\b|\bhow (?:heavy|big|large)\b", re.I)
_CODE_WORDS = re.compile(r"\bpython\b|\bmicropython\b|\bscript\b|\bsource\b|\bsnippet\b|\bimplementation\b|\bc\+\+\b|\barduino\b|(?<!block )\bcodes?\b(?! block)", re.I)
_LED_Q = re.compile(r"\bled[ _]?([123])\b", re.I)
_DEF_CUE = re.compile(r"\b(?:do|does|work|works|working|is|are|mean|means|for|use|used|measure|measures)\b", re.I)
_CALC_Q = re.compile(r"\bformula\b|\bequation\b|\bcalculat\w*|\bcompute\w*|\bconvert\w*|\bhow (?:is|are) [^?.!]{0,40}(?:measured|calculated|computed|found|worked out|determined)\b"
                     r"|\b(?:explain|describe)\b[^?.!]{0,40}\b(?:in detail|in depth|concept|principle|physics|maths?|working)\b|\bin (?:detail|depth)\b"
                     r"|\b(?:how|what)\b[^?.!]{0,50}\b(?:read|get|find|measure|display|show|work out)\b[^?.!]{0,40}\bin (?:centi\w*|cm|met(?:er|re)s?|mm|inch\w*|celsius|fahrenheit|\u00b0c|kelvin|ppm|percent|%|lit(?:er|re)s?|ml|volts?|amps?|kg|grams?|pascals?|hpa|lux|db)\b"
                     r"|\b(?:which|what) units?\b|\bunits? (?:of|for|is|are)\b", re.I)
_REWRITE_Q = re.compile(r"\bsimplif\w*|\bsimpler\b|\bsimply\b|\bsimple (?:words|terms|way|language|english)\b|\beasier\b|\beasy to understand\b|\bshorter\b|\bshorten\b|\bbrief(?:ly)?\b|\bsummari[sz]e\b|\btl;?dr\b|\beli5\b"
                         r"|\brephrase\b|\breword\b|\belaborate\b|\bexpand\b|\bmore (?:detail|details|about (?:it|that|this))\b|\bin (?:more )?detail\b|\b(?:give|show) (?:me )?(?:an? )?(?:another )?(?:example|analogy)\b|\bfor example\b"
                         r"|\b(?:explain|say|tell me|describe)\b[^?.!]{0,12}\b(?:again|better|differently|more|once more)\b|\b(?:i )?(?:did ?n[o'\u2019]?t|don[\u2019']?t|do not|can[\u2019']?t) (?:understand|get)\b|\bconfus\w*\b"
                         r"|\bwhat do you mean\b|\bcan you repeat\b|\bonce more\b|\bsay (?:that|it) again\b", re.I)
_YN_START = re.compile(r"^\W*(?:please |so |and |but |ok |okay )*(?:do|does|did|is|are|can|could|should|will|would|must|has|have|am)\b", re.I)
_NEED_Q = re.compile(r"\b(?:do|does|will|would|should) (?:i|we|you|it|my \w+) (?:really |actually |also |still )?(?:need|have to use|have to|must use)\b|\bmust i\b"
                     r"|\b(?:is|are) (?:it|they|this|that|the [\w\s().-]{1,45}?|a [\w\s().-]{1,45}?)\s*(?:block )?(?:needed|necessary|required|compulsory|mandatory|optional)\b|\bis it (?:needed|necessary|required|optional)\b", re.I)
_EXISTS_Q = re.compile(r"\bis there (?:a|an|any)\b[^?.!]{0,50}\bblocks?\b|\bare there (?:any )?blocks\b|\bdo(?:es)? (?:the |a |an )?[\w\s-]{1,40}?\s(?:have|has) (?:any |a |an )?blocks?\b", re.I)
_IFACE_Q = re.compile(r"\b(i2c|uart|analog|digital|usb)\b", re.I)
_CAN_CONNECT_Q = re.compile(r"\bcan i (?:connect|use|plug|wire|attach|hook)\b|\bis it possible to (?:connect|use)\b|\bdoes (?:the |a )?[\w\s().-]{1,40}? (?:work|connect) (?:with|to|on)\b", re.I)
_BLOCK_Q = re.compile(r"\b(?:what|how|explain|does|do|tell|describe|purpose|used for|for)\b", re.I)

_LESSON_WORDS = re.compile(r"\bled[ _]?[123]\b|\bheart ?rate\b|\bbpm\b|\bspo2\b|\boxygen\b|\bfinger\b|\boximeter\b|\blesson\b|\bprogram\b|\bthis project\b|\bour project\b", re.I)
GENERAL_CUE = re.compile(r"^\W*(?:what is|what are|what's|whats|what does [^?]{1,40} mean|define|explain|how does [^?]{1,50} work|how do [^?]{1,50} work|why (?:do|does|is|are)|"
                         r"difference between|meaning of|tell me about|what do you mean by|can you explain)\b", re.I)
GENERAL_TOPIC = re.compile(r"\b(?:spo2|sp02|oxygen saturation|blood oxygen|oxygen|bpm|heart ?rate|heartbeat|pulse|i2c|i²c|uart|serial|gpio|led|leds|relay|micropython|python|iot|aiot|iiot|"
                           r"internet of things|gateway|cloud|sensor|sensors|esp32|block coding|block programming|variable|variables|loop|if else|voltage|current|resistor|sda|scl|pins?|wires?|ground|vcc|gnd|pwm|adc|breadboard|circuit|"
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
            if b.order:  # block sheet entries are found by _block_catalog_answer (titles repeat across sensors: "Get Temperature")
                continue
            for al in [b.title, *b.aliases]:
                self._add(al, "block", b.id)
        self._build_palette()
        for kit in self.k.kits.values():
            for al in [kit.name, *kit.aliases]:
                self._add(al, "kit", kit.id)
        self._strong.sort(key=lambda t: -len(t[0].pattern))

    # ------------------------------------------------------------------ block palette (the block sheet)
    def _build_palette(self) -> None:
        self._sheet = sorted((b for b in self.k.blocks.values() if b.order), key=lambda b: b.order)
        self._cats: dict[str, list[str]] = {}        # category -> sub-categories in sheet order ("" = the category has blocks of its own)
        for b in self._sheet:
            if b.sub_category not in self._cats.setdefault(b.category, []):
                self._cats[b.category].append(b.sub_category)
        self._cat_alias = {"functions": "Functions", "function": "Functions", "logic": "Logic", "mobile": "Mobile", "loops": "Loops", "loop": "Loops",
                           "math": "Math", "text": "Text", "variables": "Variables", "variable": "Variables", "common actions": "Common Actions-ESP32",
                           "common action": "Common Actions-ESP32", "common": "Common Actions-ESP32"}
        self._cat_alias = {a: c for a, c in self._cat_alias.items() if c in self._cats}
        self._sub_names: dict[str, str] = {}          # normalised sub-category / sensor name -> sub-category
        for b in self._sheet:
            if b.sub_category:
                self._sub_names[norm(b.sub_category)] = b.sub_category
                self._sub_names[norm(re.sub(r"\s*(?:sensor|display|board|controller)$", "", b.sub_category, flags=re.I))] = b.sub_category
        self._sub_names.pop("pulse oximeter", None)  # handled through the sensor entity (three versions)
        self._sub_names.pop("motor", None)

    def _subs_for(self, e: Entities, msg: str) -> list[str]:
        """The block sub-categories a message is about: through a detected sensor, or by the sub-category's own name."""
        subs: list[str] = []
        for sid in e.sensors:
            for b in self._sheet:
                if b.sensor == sid and b.sub_category not in subs:
                    subs.append(b.sub_category)
        t = norm(msg)
        for name, sub in sorted(self._sub_names.items(), key=lambda kv: -len(kv[0])):
            m = re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", t) if len(name) >= 3 else None
            if m and sub not in subs:
                short = name != norm(sub)  # "sound" for "Sound Sensor": not when a task verb comes first ("play a sound", "detect motion")
                if short and re.search(r"\b(?:play|make|detect|measure|read|sense|check|find|monitor|control|show|display|turn|rotate|move|catch|know)\b", t[:m.start()]):
                    continue
                subs.append(sub)
        return subs

    @staticmethod
    def _place(b) -> str:
        return f"{b.category} > {b.sub_category}" if b.sub_category else b.category

    def _group(self, cat: str, sub: str):
        return [b for b in self._sheet if b.category == cat and b.sub_category == sub]

    @staticmethod
    def _md_list(bl) -> str:
        """Numbered list for the chat: bold block name, then what it does."""
        return "\n".join(f"{i}. **{b.title}**: {b.description}" for i, b in enumerate(bl, 1))

    def _block_rows(self, bl) -> list[dict]:
        return [{"order": i, "title": b.title, "description": b.description} for i, b in enumerate(bl, 1)]

    def find_blocks(self, msg: str, e: Entities) -> list:
        """Blocks of the sheet named in the message: the title as a phrase, or all its meaningful words."""
        t = " " + norm(msg) + " "
        stop = {"on", "off", "a", "an", "the", "at", "as", "of", "to", "in", "for", "with", "from", "and", "or", "pin", "value", "set", "sensor", "block", "blocks"}
        hits, partial = [], []
        for b in self._sheet:
            nt = norm(re.sub(r"\(.*?\)", " ", b.title))
            if len(nt) < 3:
                continue
            if f" {nt} " in t or any(f" {norm(al)} " in t for al in b.aliases):
                hits.append((2, len(nt), b))
                continue
            words = [w for w in nt.split() if w not in stop]
            if len(words) >= 2 and all(f" {w} " in t for w in words):
                hits.append((1, len(nt), b))
            elif len(words) >= 3:  # most of the title's words ("calculate co value" for "Calculate CO value in ppm"): only used when nothing matches fully and one block stands out
                got = sum(f" {w} " in t for w in words)
                if got >= 2 and got / len(words) >= 0.6:
                    partial.append((got / len(words), got, b))
        if not hits and partial:
            partial.sort(key=lambda x: (-x[0], -x[1]))
            if len(partial) == 1 or partial[0][0] > partial[1][0]:
                return [partial[0][2]]
        if not hits:
            return []
        best = max(h[0] for h in hits)
        hits = [h for h in hits if h[0] == best]
        longest = max(h[1] for h in hits)
        out = [h[2] for h in hits if h[1] >= longest - 12]  # a longer title ("Get Climate Sensor Value from Pin") beats the short one it contains
        if len(out) > 1:  # the full title including its parentheses, "Set Buzzer (named) as Output at Pin", beats its plain sibling
            exact = [b for b in out if f" {norm(b.title)} " in t]
            out = exact or out
        subs = self._subs_for(e, msg)
        narrowed = [b for b in out if b.sub_category in subs]
        return narrowed or out

    def _block_catalog_answer(self, msg: str, e: Entities) -> AppAnswer | None:
        """Where a block is, which blocks a category / sensor has, and what a block does, from the block sheet (0 tokens)."""
        if not self._sheet or not re.search(r"\bblocks?\b|\bblock codes?\b|\btoolbox\b|\bpalette\b", msg, re.I) or _CODE.search(msg) and self.k.blocks and e.blocks:
            return None
        src = ["Block sheet"]
        total = len(self._sheet)
        # 0) a request for the code behind a block: the sheet has none, so the answer is fixed ("block code" alone means "blocks", not Python)
        if _CODE_WORDS.search(msg) and not e.blocks:
            bl = self.find_blocks(msg, e)
            if not bl:
                own = self.detect(msg)
                bl = [b for sub in self._subs_for(own, msg) for b in self._group(self._sheet_cat(sub), sub)]
            if bl:
                return self._block_code_answer(bl)
        # 1) what exists at all
        if re.search(r"\bhow many\b[^?.!]{0,30}\bblocks?\b|\b(?:categories|groups|sections|drawers)\b[^?.!]{0,30}\bblocks?\b|\bblocks?\b[^?.!]{0,30}\b(?:categories|groups|sections|drawers)\b|\bwhat (?:kinds?|types?) of blocks\b", msg, re.I) and not self._subs_for(e, msg):
            lines = []
            for c, subs in self._cats.items():
                if subs == [""]:
                    lines.append(f"- **{c}**: {len(self._group(c, ''))} blocks")
                else:
                    lines.append(f"- **{c}**: {sum(len(self._group(c, x)) for x in subs)} blocks, in {len(subs)} groups (one per sensor or device)")
            return AppAnswer("blocks", f"**The block editor has {total} blocks** in these categories:\n\n" + "\n".join(lines)
                             + "\n\nAsk me about a category (for example ‘Math’) or a sensor (for example ‘the buzzer’) and I will list its blocks.", src)
        # 2) where is a block / what does a block do
        named = self.find_blocks(msg, e)
        where_q = re.search(r"\bwhere\b|\bwhich (?:category|group|section|drawer|menu|sub-?category)\b|\bwhat (?:category|group|section|drawer)\b|\b(?:find|found|locate|located|situated|under|belongs?)\b", msg, re.I)
        what_q = _BLOCK_Q.search(msg) or where_q or _NEED_Q.search(msg)
        if named and what_q:
            subs = {b.sub_category for b in named}
            if len(named) > 1 and len(subs) == 1:
                return AppAnswer("blocks", f"**{len(named)} blocks match.** They are all in **{self._place(named[0])}**:\n\n" + self._md_list(named), src, footer=PALETTE_FOOTER)
            if len(named) > 1 and len(subs) > 1:
                places = "\n".join(f"- {self._place(b)}" for b in named)
                text = f"**{named[0].title}** exists in {len(named)} places:\n\n{places}\n\nTell me which sensor you mean and I will show you that block."
                return AppAnswer("block", text, src)
            b = named[0]
            grp = self._group(b.category, b.sub_category)
            idx = grp.index(b) + 1
            where_line = f"**{b.title}** is in **{self._place(b)}** (block {idx} of {len(grp)} in that group)."
            asks_what = bool(re.search(r"\bwhat\b|\bexplain\b|\bdescribe\b|\bpurpose\b|\bused for\b|\bhow (?:does|do|is|to)\b|\bmeaning\b", msg, re.I))
            asks_group = bool(re.search(r"\bgroup\b|\border\b|\bnext\b|\bafter\b|\bbefore\b|\bwith it\b|\balongside\b|\bsiblings?\b", msg, re.I))
            if where_q and not asks_what:
                text = where_line
            elif asks_what and not where_q:
                text = f"**{b.title}**: {b.description}"
            else:
                text = f"{where_line}\n\n**What it does:** {b.description}"
            if asks_group and b.sub_category and len(grp) > 1:
                text += "\n\n**The group, in order:**\n\n" + "\n".join(f"{i}. " + (f"**{x.title}** (this one)" if x is b else x.title) for i, x in enumerate(grp, 1))
            return AppAnswer("block", text, [f"Block: {b.title}"])
        # 3) the blocks of one sensor / sub-category
        # a sensor carried over from the previous turn only counts for a real follow-up ("which blocks does it need?"), never for a message that asks about a block we could not find
        own = self.detect(msg)
        follow_up = bool(re.search(r"\b(?:it|its|that|this|them|those|these|same)\b", msg, re.I)) and not (where_q and re.search(r"\bblock\b", msg, re.I))
        subs = self._subs_for(own if own.sensors else (e if follow_up else own), msg)
        if subs and not self._cat_named(msg):
            bl = [b for sub in subs for b in self._group(self._sheet_cat(sub), sub)]
            names = " and ".join(subs)
            text = (f"**{names}**: {len(bl)} block{'s' if len(bl) != 1 else ''} (in the block editor under {self._sheet_cat(subs[0])} \u203a {subs[0]}).\n\n"
                    "In this order, from the top:\n\n" + self._md_list(bl))
            sid = next((b.sensor for b in bl if b.sensor in self.k.sensors and self.k.sensors[b.sensor].kit_connections), None)
            return AppAnswer("blocks", text, sources=[f"Blocks: {self._sheet_cat(subs[0])} > {n}" for n in subs])
        # 4) a whole category
        cat = self._cat_named(msg)
        if cat:
            bl = [b for b in self._sheet if b.category == cat]
            return AppAnswer("blocks", f"**{cat}**: {len(bl)} blocks, in the order they appear in the editor:\n\n" + self._md_list(bl), [f"Block category: {cat}"], footer=PALETTE_FOOTER)
        if _BLOCKS.search(msg) and not e.sensors and not named and not [w for w in norm(msg).split() if w not in self._TASK_STOP]:  # "Which blocks do I need?": which task or sensor?
            cats = "\n".join(f"- {c.replace('-ESP32', '')}" for c in self._cats if c != "Sensors-ESP32")
            return AppAnswer("blocks", f"**I know {total} blocks.** Which ones do you need?\n\n**Tell me a sensor**, for example ‘the buzzer’ or ‘the ultrasonic sensor’, or **a category**:\n\n{cats}\n- Sensors (one group per sensor)", src)
        # 5) which sensors have blocks
        if re.search(r"\bsensors?\b", msg, re.I) and re.search(r"\b(?:which|what|list|show|all)\b", msg, re.I) and not e.sensors:
            subs = [x for x in self._cats.get("Sensors-ESP32", []) if x]
            return AppAnswer("blocks", f"**{len(subs)} sensors and devices have blocks** (category Sensors-ESP32):\n\n" + ", ".join(subs) + "\n\nAsk me for the blocks of any one, for example ‘Which blocks do I need for the flame sensor?’", src)
        return None

    _TASK_STOP = frozenset("a an the to of for in on at is are do does i we you my me need needed use used using which what block blocks code want how can should would will "
                           "make get with and or it this that sensor sensors thing something program build create by from be as into when".split())

    def _carried_sensors(self, msg: str, e: Entities) -> list[str]:
        """Sensors the message is about: the ones it names; those carried over from the conversation only for a real follow-up ("it", "that sensor")."""
        own = self.detect(msg).sensors
        if own:
            return own
        return list(e.sensors) if re.search(r"\b(?:it|its|that|this|them|those|these|same)\b", msg, re.I) else []

    _FOLLOW_BLOCKS = re.compile(r"\b(?:explain|describe|tell me (?:more )?about|what (?:do|does)|how (?:do|does)|show)\b[^?.!]{0,30}\b(?:those|these|them|the above|that)\b[^?.!]{0,15}\bblocks?\b"
                                r"|\b(?:explain|describe)\s+(?:those|these|them)\b", re.I)

    def follow_up(self, message: str, previous_user: str) -> AppAnswer | None:
        """"Explain those blocks": the blocks of the previous palette answer again, each with what it does (0 tokens)."""
        if not self._sheet or not previous_user or not self._FOLLOW_BLOCKS.search(message):
            return None
        r = self.answer(previous_user, self.detect(previous_user), Entities())
        return r if r and r.kind in ("blocks", "block") else None

    # ---- wiring hazards: tell the student to stop / not to do it, then show the correct wiring ----------------------------
    def hazard_answer(self, message: str, e: Entities) -> AppAnswer | None:
        found = detect_hazard(message)
        if not found:
            return None
        hz, is_question = found
        if is_question:
            head, body = "## \u26d4 No, do not do this", hz.never
        elif hz.fire:
            head, body = "## \u26d4 Get away and tell an adult now", hz.fix
        elif hz.id == "shock":
            head, body = "## \u26d4 Tell an adult right now", hz.fix
        else:
            head, body = "## \u26d4 Stop! Unplug the power now", hz.fix
        out = [head, body]
        sensors = [self.k.sensors[x] for x in e.sensors if x in self.k.sensors and self.k.sensors[x].kit_connections]
        if hz.id in ("short_circuit", "power_to_signal", "overvoltage"):
            if sensors:
                connect, _src = self._guide_connect(sensors[0], e.kits)
                out += [f"### The correct wiring for the {sensors[0].name.split(' (')[0]}", connect]
            else:
                out += ["### The simple rule", "- **GND** \u2192 GND (ground) only\n- **VCC** \u2192 the voltage written for your sensor (3.3V or 5V)\n- **Signal / data wire** \u2192 a GPIO pin\n\nTell me which sensor you use and I will show its exact wiring."]
        return AppAnswer("safety_stop", "\n\n".join(out), ["Safety rules"], grounded=True)

    # ---- yes / no questions: the verdict first, then the details -------------------------------------------------------
    @staticmethod
    def _lower_first(t: str) -> str:
        return t[:1].lower() + t[1:] if t and not t[:2].isupper() else t

    def _need_verdict(self, b) -> str:
        """"Do I need the X block?" from the block's group and purpose (the sheet does not say 'required', so this follows how the group is built)."""
        t, d = b.title, b.description.rstrip(".")
        if b.category != "Sensors-ESP32" and b.title not in ("Start", "Use [Sensor]"):
            return f"**It depends.** Use the {t} block only if your program needs it: {self._lower_first(d)}."
        if t == "Start":
            return f"**No.** {d}."   # the course owner: Start is for the reader, the code works without it
        if t.startswith("Use ["):
            return f"**Yes.** {d}."
        if "several" in d or "(named)" in t:
            return f"**No, not for a single device.** {d}. The simpler block in the same group does the job for one."
        if re.match(r"(?:Set |Initialize|Take Picture)", t):
            return f"**Yes.** This is the set-up block of the group: {self._lower_first(d)}. The other blocks of the group need it first."
        if t.startswith("Calculate") or "Calculate" in t:
            return f"**Yes.** {d}. Put it before the block that gets the result."
        if re.match(r"(?:Get|Read)\b", t):
            return f"**Yes, if you want the reading.** {d}."
        return f"**It depends on your project.** {d}."

    def _yes_no_lead(self, msg: str, e: Entities) -> str | None:
        """One line that answers a yes/no question, or None when the files cannot decide it."""
        # a) "Do I need the Calculate Distance block?"
        if self._sheet and _NEED_Q.search(msg):
            named = self.find_blocks(msg, e)
            if named and len({b.sub_category for b in named}) == 1:
                verdicts = [self._need_verdict(b) for b in named]
                if len({v.split("**")[1] for v in verdicts}) == 1:
                    b0 = named[0]
                    grp = self._group(b0.category, b0.sub_category)
                    return verdicts[0] + f"\n\n**Where:** {self._place(b0)} (block {grp.index(b0) + 1} of {len(grp)})."
        # b) "Is there a block for the buzzer?" / "Does the LoRa have blocks?"
        if self._sheet and _EXISTS_Q.search(msg):
            subs = self._subs_for(self.detect(msg), msg)
            if subs:
                n = sum(len(self._group(self._sheet_cat(x), x)) for x in subs)
                titles = ", ".join(b.title for x in subs for b in self._group(self._sheet_cat(x), x))
                return f"**Yes.** The {' and '.join(subs)} has {n} block{'s' if n != 1 else ''} in the block editor: {titles}."
        sensors = [self.k.sensors[x] for x in self.detect(msg).sensors if x in self.k.sensors]
        # c) "Does the pulse oximeter use I2C?"
        m = _IFACE_Q.search(msg)
        if m and sensors and re.match(r"^\W*(?:do|does|is|are)\b", msg, re.I):
            s0, want = sensors[0], m.group(1)
            have = {s0.interface.lower(), *(kc.interface.lower() for kc in s0.kit_connections if kc.interface)} - {"none", "-", ""}
            if have:
                if want.lower() in have:
                    per_kit = "; ".join(f"{self._kit_short(kc.kit)}: {kc.interface}" for kc in s0.kit_connections if kc.interface)
                    return f"**Yes.** The {s0.name.split(' (')[0]} uses {want.upper() if want.lower() in ('i2c', 'uart', 'usb') else want.lower()}" + (f" ({per_kit})." if len(have) > 1 and per_kit else ".")
                return f"**No.** The {s0.name.split(' (')[0]} uses {', '.join(sorted(h.upper() if h in ('i2c', 'uart', 'usb') else h for h in have))}, not {want.upper() if want.lower() in ('i2c', 'uart', 'usb') else want.lower()}."
        # d) "Can I connect the ultrasonic sensor to the ESP32 kit?"
        if sensors and _CAN_CONNECT_Q.search(msg) and sensors[0].kit_connections:
            s0, kits = sensors[0], self.detect(msg).kits
            have = [kc.kit for kc in s0.kit_connections]
            if kits:
                if kits[0] in have:
                    return f"**Yes.** The {s0.name.split(' (')[0]} is documented for the {self._kit_short(kits[0])}."
                return f"**I can't confirm that.** The {s0.name.split(' (')[0]} has no documented connection for the {self._kit_short(kits[0])}."
            return f"**Yes.** The {s0.name.split(' (')[0]} is documented for: " + ", ".join(self._kit_short(k) for k in have) + "."
        return None

    def _sheet_cat(self, sub: str) -> str:
        return next((b.category for b in self._sheet if b.sub_category == sub), "")

    def _cat_named(self, msg: str) -> str | None:
        t = norm(msg)
        for alias, cat in sorted(self._cat_alias.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"(?<![a-z0-9]){alias}(?![a-z0-9])[^?.!]{{0,12}}(?:category|blocks?|drawer|group|section)|(?:category|drawer|group|section)\s+{alias}\b|\b{alias}\s+blocks?\b", t):
                return cat
        return None

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
        return self.apps[a].title if a in self.apps else ""

    # ------------------------------------------------------------------ routing helpers
    def claims(self, message: str, e: Entities, recent: Entities | None = None) -> bool:
        """A message that names a generic gateway device ("LED") but is clearly about the lesson ("LED 2", "heart rate", "the program"),
        or follows a conversation about the lesson ("what about the LED?")."""
        return bool(e.weak and (_LESSON_WORDS.search(message) or (recent is not None and recent.strong)))

    _NOT_PHENOMENON = frozenset("sensor sensors module modules kit board controller gateway display motor pump input output analog digital block blocks code program "
                                "pins wires wire wiring connect connection application project lesson ground power supply".split())

    def is_rewrite_request(self, message: str, e: Entities) -> bool:
        """"simplify it", "explain that again", "give me an example", "shorter please": the student wants the PREVIOUS ANSWER reworked.
        Only short messages that name nothing new (no sensor, kit, block, wiring)."""
        if e.strong or e.sensors or len(message.split()) > 12:
            return False
        if re.search(r"\b(?:connect\w*|wir(?:e|ing)|pins?|blocks?|code|gpio)\b", message, re.I):
            return False
        return bool(_REWRITE_Q.search(message))

    def explain_target(self, message: str, e: Entities, recent: Entities) -> str | None:
        """"How does the ultrasonic sensor measure distance?", "now explain the same concept in detail and how can I read the distance in centimetres":
        the sensor whose working principle, formula and calculation is asked (from the message, or the one just discussed)."""
        if not _CALC_Q.search(message):
            return None
        if _WIRING.search(message) and not re.search(r"\bformula\b|\bcalculat\w*|\bconvert\w*|\bunits?\b|\bin detail\b|\bconcept\b|\bprinciple\b", message, re.I):
            return None   # a wiring question
        named = [x for x in e.sensors if x in self.k.sensors]
        if not named and re.search(r"\b(?:same|that|it|this|those|concept|sensor)\b", message, re.I):
            named = [x for x in recent.sensors if x in self.k.sensors]
        named = [x for x in named if self.k.sensors[x].working or self.k.sensors[x].description]
        return named[0] if named else None

    def explain_notes(self, sensor_id: str) -> str:
        """What is DOCUMENTED about the sensor, without pins or wiring: the model explains the formula from general physics around it."""
        s = self.k.sensors[sensor_id]
        lines = [f"SENSOR NOTES: {s.name} ({self._cat(s)}).", f"Working (from the sensor list): {s.working or s.description}"]
        if s.how_it_measures:
            lines.append("How the block code measures: " + " ".join(s.how_it_measures))
        bl = [b for b in self._sheet if b.sensor == s.id]
        if bl:
            lines.append("Blocks: " + "; ".join(f"{b.title}: {b.description}" for b in bl))
        if s.variables:
            lines.append("Variables: " + ", ".join(s.variables))
        return "\n".join(lines)

    def is_platform_question(self, message: str, e: Entities) -> bool:
        """"Tell me about Grovator", "what is this platform?": about the platform itself, not a sensor, kit or page."""
        if e.strong or e.sensors or e.kits:
            return False
        if re.search(r"\b(?:connect\w*|wir(?:e|ing)|pins?|blocks?|code|sensors?|kit)\b", message, re.I):
            return False
        return bool(re.search(r"\bgrovator\b|\b(?:this|the) (?:platform|course|website|app|application)\b", message, re.I)
                    and re.search(r"\b(?:tell me about|what(?:'s| is)|who|explain|describe|about|introduce|what can i|how does|how do (?:i|we) use)\b", message, re.I))

    def is_phenomenon_question(self, message: str, e: Entities) -> bool:
        """"What is alcohol?", "what is soil?": a question about the substance or physical quantity a sensor measures, NOT about the sensor.
        Answered from general knowledge, never with the sensor's own description."""
        if e.strong or not GENERAL_CUE.search(message):
            return False
        if re.search(r"\b(?:sensors?|modules?|devices?|kits?|boards?|controllers?|gateway|connect\w*|wir(?:e|ing)|pins?|blocks?|this|these|it|its|here|page|lesson|project|application)\b", message, re.I):
            return False
        words = {w for w in norm(message).split() if len(w) >= 4}
        names = {w for s in self.k.sensors.values() for al in [s.name, *s.aliases] for w in norm(al).split() if len(w) >= 4} - self._NOT_PHENOMENON
        return bool(words & names)

    def is_general(self, message: str, e: Entities, hw_named: bool) -> bool:
        """A "what is X / how does X work" question about a technology: answered from model knowledge in student language."""
        if e.strong or not (GENERAL_CUE.search(message) and GENERAL_TOPIC.search(message)):
            return False
        # a comparison of two ideas ("difference between a sensor and an LED") is general knowledge even when a gateway device is named
        return not hw_named or bool(re.search(r"\bdifference between\b|\bversus\b|\bvs\.?\b", message, re.I))

    # ------------------------------------------------------------------ answers
    def answer(self, message: str, e: Entities, recent: Entities, hw_named: bool = False, page_only: bool = False) -> AppAnswer | None:
        """A deterministic answer, or None to fall through to retrieval / the model. A yes/no question gets its verdict FIRST ("**Yes.** ...")."""
        r = self._answer_inner(message, e, recent, hw_named, page_only)
        if (r is None or r.kind != "not_documented") and _YN_START.search(message) and not (r is not None and r.kind in ("safety_stop", "yes_no")):
            lead = self._yes_no_lead(message, e)
            if lead:   # a yes/no question gets the verdict and at most one supporting line: not the whole detail answer
                sid = (self.detect(message).sensors or [None])[0]
                src = r.sources if r is not None else ([f"{self.k.sensors[sid].name} > Sensor details"] if sid else ["Block sheet"])
                return AppAnswer("yes_no", lead, src)
        if r is not None and r.kind == "blocks" and _WIRING.search(message):   # "how do I connect X and which blocks do I need?": both parts were asked
            own = [self.k.sensors[x] for x in self.detect(message).sensors if x in self.k.sensors and self.k.sensors[x].kit_connections]
            if own:
                conn = self._guide_connect(own[0], self.detect(message).kits)[0]
                r.text = f"### How to connect the {own[0].name.split(' (')[0]}\n\n{conn}\n\n### Blocks\n\n{r.text}"
        return r

    def _answer_inner(self, message: str, e: Entities, recent: Entities, hw_named: bool = False, page_only: bool = False) -> AppAnswer | None:
        """The deterministic answer builders (see answer())."""
        if _SPEC_Q.search(message) and (e.sensors or e.kits or re.search(r"\b(?:sensor|kit|controller|module|board)\b", message, re.I)):
            return AppAnswer("not_documented", NOT_DOCUMENTED + " (The sensor and kit documents do not give weight, size or price.)", ["Sensor list"])
        palette = self._block_catalog_answer(message, e)
        if palette:
            return palette
        if not (hw_named and not page_only and not e.strong):  # a gateway device NAMED in the message (not just an open page) keeps the old flow
            cat = self._catalog_answer(message, e)
            if cat:
                return cat
        if not self.apps:  # generic knowledge base: sensors, kits and blocks only (applications can be added later as files)
            return self._generic_answer(message, e)
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
                return self._sensor_guide(s, e.kits, what=bool(re.search(r"\bexplain\b|\bdescribe\b|\btell me about\b|\bwhat (?:is|are)\b|\bhow (?:does|do)\b[^?.!]{0,40}\bwork", msg, re.I)),
                                          connect=True, program=bool(re.search(r"\bblocks?\b|\bprogram\b|\bcode\b|\bsteps?\b|\bbuild\b|\bcreate\b|\bmake\b|\bproject\b|\bapplication\b", msg, re.I)))
            fact = self._sensor_fact(None, e, msg)
            if fact:
                return fact
            if _EXAMPLE.search(msg) or (_CODE.search(msg) and s.example_code and sid not in self._lesson_sensors() and not s.blocks):
                return self._sensor_example(s)
            if _ABOUT.search(msg) and not _TROUBLE.search(msg) and not _CALC_Q.search(msg):   # formula / calculation questions go to the explain mode
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

    # ---- structured sensor guide: what it is, how to connect, how it fits the block program ------------------------------
    def _guide_connect(self, s, kit_ids: list[str]) -> tuple[str, list[str]]:
        """"How to connect" section: one block per kit with a bullet per pin. Returns (markdown, source names)."""
        shown = [kc for kc in s.kit_connections if not kit_ids or kc.kit in kit_ids]
        parts = []
        for kc in shown:
            head = f"**{self._kit_short(kc.kit)}**" + (f" ({kc.interface})" if kc.interface and kc.interface.lower() not in ("none", "-") else "")
            pins = "\n".join(f"- **{p.pin}** \u2192 {p.connects_to}" + (f" ({p.note})" if p.note else "") for p in kc.pins) or "- no pins are given"
            extra = []
            hint = self._digital_hint(s, kc.kit)
            if hint:
                extra.append(f"Usable {hint.strip()[1:-1]}")
            if kc.note:
                extra.append(f"Note: {kc.note.rstrip('.')}.")
            if kc.warning:
                extra.append(f"Warning: {kc.warning}")
            parts.append(head + "\n" + pins + ("\n\n" + "\n\n".join(extra) if extra else ""))
        missing = [x for x in ("esp32_kit", "govator_controller_pi") if x not in {kc.kit for kc in shown} and (not kit_ids or x in kit_ids)]
        if missing:
            parts.append("**Not documented:** " + ", ".join(self._kit_short(x) for x in missing) + " (this sensor's connection for that kit is not in my files).")
        if not kit_ids and len(shown) > 1:
            parts.append("Tell me which kit you use if you want only one.")
        return "\n\n".join(parts), [f"{s.name} > Sensor details"]

    def _guide_program(self, s) -> str:
        """"Typical program" section from the block sheet: Start, Use [sensor], the sensor's own blocks in order, then use the reading."""
        bl = [b for b in self._sheet if b.sensor == s.id]
        if not bl:
            return ""
        use = next((b for b in self._sheet if b.title.startswith("Use [")), None)
        steps = []   # the Start block is only a reader's marker: the program works without it, so it is not a step
        if use:
            steps.append(f"**Use [sensor]** (Common Actions): {use.description} Pick the {s.name.split(' (')[0]} in the dropdown if it is listed.")
        steps += [f"**{b.title}**: {b.description}" for b in bl]
        steps.append("Use the reading: keep it in a variable (Variables \u203a Set \u2026 to) and show it with a Print block (Text \u203a Print), or send it to the mobile app with a Print text to mobile block (Mobile).")
        return "\n".join(f"{i}. {t}" for i, t in enumerate(steps, 1))

    def _guide_facts(self, s) -> str:
        facts = []
        if s.i2c:
            facts.append(f"I2C address: {s.i2c.address}" + (f" (SCL {s.i2c.scl}, SDA {s.i2c.sda})" if s.i2c.scl else ""))
        for kc in s.kit_connections:
            if kc.example_code:
                facts.append(f"Example program for the {self._kit_short(kc.kit)}: {kc.example_code} (its code is not in my files)")
        if s.example_code and not any(kc.example_code for kc in s.kit_connections):
            facts.append(f"Example program: {s.example_code} (its code is not in my files)")
        return "\n".join(f"- {f}" for f in facts)

    def _sensor_guide(self, s, kit_ids: list[str], what: bool = False, connect: bool = True, program: bool = False) -> AppAnswer:
        """Only the parts the student asked for: what it is, how to connect it, the block program. Nothing else."""
        name = s.name
        if connect and kit_ids and not [kc for kc in s.kit_connections if kc.kit in kit_ids]:
            return AppAnswer("not_documented", f"I don't have that detail yet: the {name} has no documented connection for "
                             + " or ".join(self._kit_short(x) for x in kit_ids) + ". Please check your kit documentation or ask your teacher.", [f"{name} > Sensor details"], grounded=True)
        src = [f"{name} > Sensor details"]
        out = [f"## {name}", f"{self._cat(s).capitalize()}" + (f" \u00b7 {s.interface} interface" if s.interface not in ("none", "") else "")]
        if what:
            out += ["### What it is", s.working or s.description]
        if connect and s.kit_connections:
            out += ["### How to connect", self._guide_connect(s, kit_ids)[0]]
        prog = self._guide_program(s) if program else ""
        if prog:
            out += ["### Typical program in the ESP32 block editor", prog]
            src.append(f"Blocks: Sensors-ESP32 > {next(b.sub_category for b in self._sheet if b.sensor == s.id)}")
        return AppAnswer("sensor_about" if what and not connect else "sensor_connect", "\n\n".join(out), src)

    def _sensor_connect(self, s, kit_ids: list[str]) -> AppAnswer:
        return self._sensor_guide(s, kit_ids, what=False, connect=True)

    def _sensor_about(self, s, kit_ids: list[str]) -> AppAnswer:
        return self._sensor_guide(s, kit_ids, what=True, connect=False)

    def _sensor_example(self, s) -> AppAnswer:
        files = [(self._kit_short(kc.kit), kc.example_code) for kc in s.kit_connections if kc.example_code] or ([("sensor list", s.example_code)] if s.example_code else [])
        text = (f"**Example programs for the {s.name}**\n\n" + "\n".join(f"- {k}: {f}" for k, f in files)
                + "\n\nI don't have their code, so I can't show it." if files else f"The sensor lists give no example program for the {s.name}. I don't have that detail yet.")
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
            block = [f"### {self._kit_short(kit.id)}"]
            if re.search(r"\bwiring rules\b", msg, re.I) and kit.wiring_rules:
                block.append("**Wiring rules**\n" + "\n".join(f"{n}. {r}" for n, r in enumerate(kit.wiring_rules, 1)))
            if rules:
                block.append("\n".join(f"- **{r.name}**: {', '.join(r.pins)}" + (f" ({r.note})" if r.note else "") for r in rules))
            if len(block) > 1:
                lines.append("\n\n".join(block))
            src.append(f"Kit: {kit.name}")
        if not lines:
            return AppAnswer("not_documented", NOT_DOCUMENTED, src or ["kits"])
        return AppAnswer("pin_rules", "\n\n".join(lines) + ("\n\nTell me which kit you use if you want only one." if len(kits) > 1 and not kit_ids else ""), src)

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
        text = f"**{len(items)} documented**\n\n" + "\n\n".join(f"**{g}**\n{', '.join(sorted(n))}" for g, n in sorted(groups.items()))
        return AppAnswer("sensor_list", text + "\n\nAsk me how any of them works or connects.", ["Sensor list"])

    # ---- generic mode (no application installed) -------------------------------------------------------
    def _generic_answer(self, message: str, e: Entities) -> AppAnswer | None:
        msg = message
        sensors = [x for x in e.sensors]
        s = self.k.sensors[sensors[0]] if sensors else None
        if _MEASURE.search(msg):
            having = [self.k.sensors[x] for x in sensors if self.k.sensors[x].how_it_measures] or [x for x in self.k.sensors.values() if x.how_it_measures]
            if len(having) == 1:
                h = having[0]
                return AppAnswer("how_it_measures", f"How the block code of the {h.name.split(' (')[0]} measures:\n" + "\n".join(f"- {t}" for t in h.how_it_measures), [f"{h.name} > How the block code measures"])
        if s:
            fact = self._sensor_fact(None, e, msg)
            if fact:
                return fact
        if e.blocks:
            b = self.k.blocks[e.blocks[0]]
            if _CODE.search(msg):
                return self._block_code_answer([b])
            if _BLOCK_Q.search(msg):
                return self._block_detail(b)
        carried = self._carried_sensors(msg, e)
        s = self.k.sensors[carried[0]] if carried else None   # a stale sensor must not answer a block question about something else
        if _BLOCKS.search(msg) or _CODE.search(msg) or (s and s.blocks and re.search(r"\bblocks?\b", msg, re.I)):
            if s and s.blocks:
                bl = [self.k.blocks[b] for b in s.blocks]
                if _CODE.search(msg) and not _BLOCKS.search(msg):
                    return self._block_code_answer(bl)
                chain = " -> ".join(f"[{b.title}]" for b in bl)
                text = (f"For the {s.name.split(' (')[0]} the documented blocks are, stacked top to bottom: {chain}. "
                        "Ask me to show the code of any block or what it does.")
                return AppAnswer("blocks", text, [f"Block: {b.title}" for b in bl],
                                 blocks=[{"order": i, "title": b.title, "description": b.description} for i, b in enumerate(bl, 1)])
        return None

    def _block_code_answer(self, bl) -> AppAnswer:
        if not any(b.code for b in bl):
            sid = next((b.sensor for b in bl if b.sensor), None)
            bl = [b for b in self.k.blocks.values() if sid and b.sensor == sid and b.code] or bl
        bl = [b for b in bl if b.code]
        if not bl:
            return AppAnswer("not_documented", "**No code available.**\n\nThe block sheet lists which blocks exist, where they are and what they do, but it gives no code for them. "
                             + NOT_DOCUMENTED + "\n\nI can show you ‘where the block is’ or ‘what it does’ instead.", ["Block sheet"])
        code = [{"title": b.title, "language": b.language, "code": b.code.rstrip(), "note": " ".join(b.code_notes)} for b in bl]
        return AppAnswer("code", "Here is the code behind " + ("this block." if len(bl) == 1 else "these blocks, in the order you stack them."),
                         [f"Block: {b.title}" for b in bl], code=code)

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
        sid = e.sensors[0] if e.sensors else (a.circuit.sensor if (a and (e.any or not e.kits)) else None)
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
