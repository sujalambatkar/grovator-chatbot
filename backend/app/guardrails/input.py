"""L1 input guardrails: deterministic, 0 LLM tokens. Runs before anything else touches the message.

Matching is done on a normalised copy (NFKC, invisible chars stripped, lower-cased, leetspeak folded)
so trivial obfuscation ("1gn0re prev1ous instruct1ons", zero-width characters) does not bypass the rules.
"""
import re
import unicodedata
from dataclasses import dataclass, field

from app.core.auth import Role
from app.guardrails.pii import detect_pii, redact
from app.llm import prompts as P

ROLE_RANK = {Role.student: 0, Role.teacher: 1, Role.admin: 2, Role.superadmin: 3}
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(" " if c in "\n\t\r" else c for c in text if unicodedata.category(c) not in ("Cf", "Cc") or c in "\n\t\r")
    return re.sub(r"\s+", " ", text).strip()


def _rx(*pats: str) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in pats]


SELF_HARM = _rx(r"\b(?:kill myself|suicid\w*|end my life|want to die|hurt myself|self[- ]?harm|cut myself)\b")

DISTRESS = _rx(
    r"\b(?:i (?:feel|am|'m|m) (?:really |so |very )?(?:sad|alone|lonely|hopeless|worthless|depressed|scared|unsafe|anxious)|"
    r"feeling (?:really |so |very )?(?:sad|alone|lonely|hopeless|worthless|depressed|scared|unsafe)|"
    r"nobody (?:likes|cares|loves)|i hate myself|(?:being )?bull(?:y|ied|ying) (?:me|by)|someone is (?:hurting|bullying|hitting) me)\b",
)

INJECTION = _rx(
    r"\b(?:ignore|disregard|forget|override|bypass|skip|drop)\b.{0,30}\b(?:previous|prior|above|earlier|all|any|your|the|these|those|every)\b.{0,30}\b(?:instructions?|rules?|prompts?|guidelines?|polic(?:y|ies)|restrictions?|guardrails?|safety|filters?)\b",
    r"\b(?:reveal|show|print|display|output|repeat|leak|tell me|give me|what (?:is|are|was|were)|dump|share)\b.{0,30}\b(?:system|initial|original|hidden|secret|your|developer)\b.{0,15}\b(?:prompts?|instructions?|rules|messages?|configuration|config|settings)\b",
    r"\brepeat\b.{0,25}\b(?:everything|all|text|words|the above)\b.{0,25}\b(?:above|before|so far)\b",
    r"\b(?:you are|you're|you will be|act as|acting as|pretend (?:to be|you are)|behave (?:as|like)|roleplay as|role-play as|from now on)\b.{0,50}\b(?:dan|unrestricted|unfiltered|jailbroken|evil|no (?:rules|restrictions|limits|filters)|without (?:any )?(?:rules|restrictions|filters|limits)|developer mode|god mode|root access)\b",
    r"\b(?:dan|jailbreak(?:ed|ing)?|developer mode|god mode|do anything now|dude mode)\b",
    r"<\|?\s*(?:im_start|im_end|system|assistant|endoftext)\s*\|?>|\[/?inst\]|<<\s*/?sys\s*>>|(?:^|\s)#{2,}\s*(?:system|instruction)|^\s*(?:system|assistant)\s*:",
    r"\b(?:base64|rot13|hex)\b.{0,30}\b(?:decode|decoded|decrypt|instructions?)\b|\bdecode (?:this|the following)\b",
    r"\b(?:new|updated|different|real|actual) (?:instructions?|rules|system prompt|directive)s?\b",
    r"\b(?:hypothetically|imagine|pretend|suppose)\b.{0,40}\b(?:no rules|no restrictions|without (?:any )?(?:rules|restrictions|limits)|you (?:could|can) (?:say|do) anything)\b",
    r"\bprompt injection\b|\bsudo\b|\bend of (?:system|instructions)\b",
)

CODE_INJECTION = _rx(
    r"\bselect\b.{0,40}\bfrom\b", r"\bdrop\s+(?:table|database)\b", r"\bdelete\s+from\b", r"\binsert\s+into\b",
    r"\bupdate\s+\w+\s+set\b", r"\bunion\s+select\b", r"\bor\s+1\s*=\s*1\b",
    r"\$(?:where|ne|gt|lt|regex|or|and|exists)\b", r"\bdb\.\w+\.(?:find|drop|delete|update|insert)",
    r"[;&|]\s*(?:rm|curl|wget|cat|bash|sh|nc|python)\b", r"\b(?:exec|eval|os\.system|subprocess|__import__)\s*\(",
    r"\.\./|/etc/passwd|\bcurl\s+https?://",
)

PRIVILEGE = _rx(
    r"\b(?:i am|i'm|im|i have become|my role is|my role (?:has )?changed to|consider me|treat me as|login as|log in as|impersonate|act as)\b.{0,25}\b(?P<role>super ?admin|admin(?:istrator)?|principal|owner|root|developer|teacher)\b",
    r"\b(?:bypass|disable|turn off|skip|ignore)\b.{0,20}\b(?:permissions?|authori[sz]ation|access control|rbac|security|auth)\b",
    r"\bgrant me\b.{0,20}\b(?:access|admin|permissions?)\b",
    r"\b(?:other|another|different)\b.{0,15}\b(?:school|teacher|org(?:ani[sz]ation)?|batch|class)(?:'s)?\b.{0,30}\b(?:students?|data|marks|reports?|scores?|details|records?)\b",
    r"\b(?:students?|data|marks|reports?|scores?|details|records?)\b.{0,30}\b(?:in|of|from|at|belonging to)\b.{0,10}\b(?:other|another|different)\b.{0,15}\b(?:school|teacher|org(?:ani[sz]ation)?|batch|class)",
    r"\b(?:students?|data|marks|reports?|scores?|users?|records?)\b.{0,30}\b(?:from|in|of|across|at)\b\s+(?:all|every|each)\b.{0,10}\b(?:schools?|organi[sz]ations?|orgs?)\b",
    r"\b(?:all|every|each)\b.{0,10}\b(?:schools?|organi[sz]ations?|orgs?)\b.{0,30}\b(?:students?|data|marks|reports?|scores?|users?)\b",
)

PII_REQUEST = _rx(
    r"\b(?:give|show|tell|list|share|send|find|dump|what(?:'s| is| are)|display)\b.{0,40}\b(?:phone|mobile|e-?mail|(?<!i2c )(?<!i²c )(?<!ip )(?<!mac )(?<!memory )(?<!slave )(?<!register )(?<!sensor )(?<!device )address(?!\s+(?:of|for)\s+(?:the\s+)?(?:\w+\s+)?(?:sensor|device|module|board|chip|oximeter|max30102|i2c|kit)\b)|aadhaa?r|passwords?|otp|login details|credentials|contact (?:number|details|info)|date of birth|dob|parents?'? (?:name|number|phone))\b.{0,40}\b(?:of|for|about|belonging)\b",
    r"\b(?:give|show|tell|list|share|send|dump|display)\b.{0,15}\b(?:me )?(?:the |all |their |his |her )?(?:phone numbers?|e-?mails?|passwords?|aadhaa?r)\b",
)

ADVICE = _rx(
    r"\b(?:should i take|dosage|prescri\w+|symptoms? of|diagnos\w+ me|medicine for|cure for|treatment for|am i sick)\b",
    r"\b(?:legal advice|sue\b|lawsuit|lawyer|court case|is it legal)\b",
    r"\b(?:invest(?:ing|ment|ments)?|stocks?|crypto(?:currency)?|bitcoin|loans?|tax advice|mutual funds?|trading)\b",
)

# Off-topic TASKS (creative writing etc.). Deterministic, 0 tokens. Deliberately narrow: "write a program / the steps" is fine.
OFF_TOPIC_TASK = _rx(
    r"\b(?:write|compose|make up|create|generate|tell|give|sing|draft)\b(?:\s+\w+){0,4}?\s+(?:poems?|poetry|stor(?:y|ies)|jokes?|songs?|lyrics|essays?|raps?|riddles?|limericks?|haiku|speech)\b",
    # role-play / make the sensor "talk": a creative task, not a gateway question (live red team fu-15: "pretend the sensor can talk")
    r"\b(?:pretend|role-?play|act (?:like|as)|imagine)\b[^?.!]{0,60}\b(?:talk|talks|speak|speaks|conversation|chat|human|person|character|alive)\b",
)

ABUSE = _rx(
    r"\b(?:fuck\w*|shit\w*|bitch\w*|asshole|bastard|dick|pussy|cunt|slut|whore|porn\w*|nude|naked|rape|sexy|sex)\b",
    r"\bkill (?:him|her|them|you|people|someone|everyone)\b",
    r"\b(?:make|build|create|assemble) (?:a |an )?(?:bomb|weapon|explosive|poison|gun)\b",
    r"\bhack(?:ing)? (?:into|someone|the (?:school|server|database))\b",
)

_B64 = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


@dataclass
class InputVerdict:
    allowed: bool
    clean: str                      # PII-redacted message: the only form that reaches the LLM, memory or logs
    reason: str | None = None       # refusal reason code
    reply: str | None = None        # fixed refusal template
    redactions: list[str] = field(default_factory=list)


def _refuse(reason: str, reply: str, clean: str) -> InputVerdict:
    return InputVerdict(False, clean, reason, reply)


def _any(rxs: list[re.Pattern], *texts: str) -> re.Match | None:
    for rx in rxs:
        for t in texts:
            if m := rx.search(t):
                return m
    return None


_MISSPELLED_BRAND = re.compile(r"\bgovator\b", re.I)


def fix_brand(text: str) -> str:
    """The platform is "Grovator"; people often type "Govator". Correct it so retrieval and the model see the real name."""
    return _MISSPELLED_BRAND.sub("Grovator", text)


def check_input(message: str, role: Role, max_chars: int = 1000) -> InputVerdict:
    text = normalise(message)
    leet = text.translate(_LEET)
    redactions = detect_pii(text)
    clean = fix_brand(redact(text))

    if len(message) > max_chars:
        return _refuse("too_long", P.GARBAGE_REPLY, clean[:max_chars])
    letters = sum(c.isalpha() for c in text)
    if letters < 1 or (len(text) >= 8 and letters / len(text) < 0.3) or re.search(r"(.)\1{24,}", text):
        return _refuse("garbage", P.GARBAGE_REPLY, clean)

    # Order matters: safety of the child first, then attacks, then policy topics.
    if _any(SELF_HARM, text, leet) or _any(DISTRESS, text):
        return _refuse("self_harm", P.SELF_HARM_REPLY, clean)
    if _B64.search(message) or _any(INJECTION, text, leet.lower()):
        return _refuse("injection", P.UNSAFE_REPLY, clean)
    if _any(CODE_INJECTION, text, leet):
        return _refuse("code_injection", P.UNSAFE_REPLY, clean)
    if m := _any(PRIVILEGE, text, leet):
        claimed = (m.groupdict().get("role") or "").lower().replace(" ", "")
        claimed_role = {"superadmin": Role.superadmin, "admin": Role.admin, "administrator": Role.admin,
                        "principal": Role.admin, "owner": Role.superadmin, "root": Role.superadmin,
                        "developer": Role.superadmin, "teacher": Role.teacher}.get(claimed)
        # A claim equal to / below the caller's real (JWT) role is harmless: "I am a teacher" from a teacher.
        if claimed_role is None or ROLE_RANK[claimed_role] > ROLE_RANK[role]:
            return _refuse("privilege_claim", P.PRIVILEGE_REPLY, clean)
    if _any(PII_REQUEST, text, leet):
        return _refuse("pii_request", P.PII_REQUEST_REPLY, clean)
    if _any(ABUSE, text, leet):
        return _refuse("abuse", P.ABUSE_REPLY, clean)
    if _any(ADVICE, text):
        return _refuse("advice", P.ADVICE_REPLY, clean)
    if _any(OFF_TOPIC_TASK, text):
        return _refuse("off_topic", P.OFF_TOPIC_REPLY, clean)
    return InputVerdict(True, clean, redactions=redactions)
