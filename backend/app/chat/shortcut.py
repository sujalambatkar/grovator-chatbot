"""Free router shortcuts (0 LLM tokens): obvious greetings and clearly self-contained platform questions.

The shortcut only ever says "platform_faq" or "smalltalk". Anything about marks/reports/students, anything
ambiguous, anything that refers back to earlier turns ("it", "that"), and anything with risky words is left
to the real router. A wrong shortcut is still caught downstream by the retrieval confidence gate, the answer
guard and the L1 rules, so the failure mode is a "no information" reply, not a leak.
"""
import re

from app.chat.router import Intent, Route
from app.llm.prompts import SMALLTALK_FALLBACK

_GREETING = re.compile(r"^\s*(?:hi+|hello+|hey+|hola|good (?:morning|afternoon|evening)|namaste)\W*$", re.I)
_THANKS = re.compile(r"^\s*(?:thanks?|thank you|thx|ok(?:ay)?|cool|great|bye|goodbye)(?: a lot| so much)?\W*$", re.I)
THANKS_REPLY = "You're welcome! Ask me anything about Govator, its kits or block coding."

# Platform-specific words only. Generic words ("if", "run", "line", "turn", "display", "log"...) are deliberately NOT here:
# they appear in follow-ups like "what if the number never changes?" and must not make a message look standalone.
_FAQ_WORDS = set("""kit kits block blocks robot robotics sensor sensors iot loop loops repeat variable variables
project projects program programs connect connecting motor motors led buzzer workspace palette lesson lessons
login password battery usb distance temperature motion simulator condition conditions forever debug blockly
govator course module""".split())
_DATA_WORDS = set("""mark marks score scores grade grades report reports progress attendance schedule timetable
batch batches student students rank leaderboard performance result results class classes school org
organisation organization teacher teachers admin users user""".split())
_ANAPHORA = re.compile(r"\b(?:it|its|that|this|those|these|them|they|he|she|him|her|again|also|same|other|another|"
                       r"what about|how about|and then|and how|and what)\b", re.I)
_RISKY = re.compile(r"\b(?:cheat|hack|bypass|steal|crack|exam|homework|answers?|filter|someone|someone'?s|friend'?s?|classmate'?s?|"
                    r"(?:his|her|their|teacher'?s|student'?s)\s+password)\b", re.I)
_QSTART = re.compile(r"^\s*(?:how|what|where|why|when|which|can|could|do|does|is|are|should|show|explain|tell|help)\b", re.I)


# "explain this lesson", "what do I do here": only meaningful together with the page the user is on
# "this"/"these" point at the page only when they are not followed by a word that names the platform itself:
# "explain THIS LESSON" is about the open page, "what is THIS PLATFORM" is not.
_NOT_THE_PAGE = r"platform|website|site|app|application|govator|system|chatbot|bot|software|company|school"
DEICTIC = re.compile(rf"\b(?:this(?!\s+(?:{_NOT_THE_PAGE})\b)|these|here|above|current|the page|the lesson|the program|step \d+)\b", re.I)


def try_shortcut(message: str, has_history: bool, last_user: str = "", on_page: bool = False) -> Route | None:
    if _GREETING.match(message):
        return Route(Intent.smalltalk, message, SMALLTALK_FALLBACK)
    if _THANKS.match(message):
        return Route(Intent.smalltalk, message, THANKS_REPLY)
    words = re.findall(r"[a-z']+", message.lower())
    if set(words) & _DATA_WORDS or _RISKY.search(message):
        return None  # the real router decides anything about people/marks or with risky wording
    if on_page and DEICTIC.search(message) and 2 <= len(words) <= 30:
        return Route(Intent.platform_faq, message.strip(), shortcut=True)  # the service anchors it to the open page
    standalone = (4 <= len(words) <= 40 and bool(_QSTART.match(message) or "?" in message) and not _ANAPHORA.search(message)
                  and bool(set(words) & _FAQ_WORDS))
    if standalone:
        return Route(Intent.platform_faq, message.strip(), shortcut=True)
    if has_history and last_user and 2 <= len(words) <= 30:
        # Follow-up ("what if the number never changes?"): the free rewrite is previous question + this message.
        # Retrieval, the confidence gate and the grounding guard still protect the answer; no model call needed.
        return Route(Intent.platform_faq, f"{last_user.strip()} {message.strip()}"[:300], shortcut=True)
    return None
