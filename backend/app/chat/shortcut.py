"""Free router shortcuts (0 LLM tokens): obvious greetings and clearly self-contained platform questions.

The shortcut only ever says "platform_faq" or "smalltalk". Anything about marks/reports/students, anything
ambiguous, anything that refers back to earlier turns ("it", "that"), and anything with risky words is left
to the real router. A wrong shortcut is still caught downstream by the retrieval confidence gate, the answer
guard and the L1 rules, so the failure mode is a "no information" reply, not a leak.
"""
import re

from app.chat.router import Intent, Route
from app.llm.prompts import HOW_ARE_YOU_REPLY, SMALLTALK_FALLBACK, WHO_ARE_YOU_REPLY

_GREETING = re.compile(r"^\s*(?:hi+|hello+|hey+|hola|good (?:morning|afternoon|evening)|namaste)\W*$", re.I)
_THANKS = re.compile(r"^\s*(?:thanks?|thank you|thx|ok(?:ay)?|cool|great|bye|goodbye)(?: a lot| so much)?\W*$", re.I)
# Whole-message chit-chat only (anchored both ends), so "How are you supposed to connect the kit?" stays a real question.
_GREET = r"(?:(?:hi|hello|hey)\W+)?"
_HOW_ARE_YOU = re.compile(rf"^\W*{_GREET}(?:how are you|how'?s it going|how do you do|what'?s up|how have you been)(?:\s+(?:doing|going|today|now|there|buddy|friend|bot))*\W*$", re.I)
_WHO_ARE_YOU = re.compile(rf"^\W*{_GREET}(?:who are you|what are you|what'?s your name|what is your name|tell me about yourself|introduce yourself|"
                          r"are you (?:a |an )?(?:bot|robot|ai|human|real|person|chatbot))(?:\s+\w+){0,2}\W*$", re.I)
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
# A message is only a FOLLOW-UP if it shows it: a pronoun (_ANAPHORA), a platform word, or a continuation cue at the start.
# Without that evidence ("my favourite colour is blue") it must not be glued onto the previous question: the router decides.
_CONTINUATION = re.compile(r"^\W*(?:and|also|then|so|but|now|next|ok(?:ay)?[ ,]+(?:so|and|now)|what if|what about|how about|why|why not)\b", re.I)
_RISKY = re.compile(r"\b(?:cheat|hack|bypass|steal|crack|exam|homework|answers?|filter|someone|someone'?s|friend'?s?|classmate'?s?|"
                    r"(?:his|her|their|teacher'?s|student'?s)\s+password)\b", re.I)
_QSTART = re.compile(r"^\s*(?:how|what|where|why|when|which|can|could|do|does|is|are|should|show|explain|tell|help)\b", re.I)


# "explain this lesson", "what do I do here": only meaningful together with the page the user is on
# "this"/"these" point at the page only when they are not followed by a word that names the platform itself:
# "explain THIS LESSON" is about the open page, "what is THIS PLATFORM" is not.
_NOT_THE_PAGE = r"platform|website|site|app|application|govator|system|chatbot|bot|software|company|school"
_NEXT_OF_THE_PAGE = (r"what(?:'s| is)? (?:comes |is )?next|what (?:should|do|can) i (?:do|learn|study|try) next|"
                     r"(?:the )?next (?:lesson|step|page|one)(?!\s+(?:after|before|from))|(?:the )?previous (?:lesson|page|one)(?!\s+(?:before|to|of))")
DEICTIC = re.compile(rf"\b(?:this(?!\s+(?:{_NOT_THE_PAGE})\b)|these|here|above|current|the page|the lesson|the program|step \d+|{_NEXT_OF_THE_PAGE})\b", re.I)


def try_shortcut(message: str, has_history: bool, last_user: str = "", on_page: bool = False) -> Route | None:
    if _GREETING.match(message):
        return Route(Intent.smalltalk, message, SMALLTALK_FALLBACK, shortcut=True)
    if _THANKS.match(message):
        return Route(Intent.smalltalk, message, THANKS_REPLY, shortcut=True)
    if _HOW_ARE_YOU.match(message):
        return Route(Intent.smalltalk, message, HOW_ARE_YOU_REPLY, shortcut=True)
    if _WHO_ARE_YOU.match(message):
        return Route(Intent.smalltalk, message, WHO_ARE_YOU_REPLY, shortcut=True)
    words = re.findall(r"[a-z']+", message.lower())
    if set(words) & _DATA_WORDS or _RISKY.search(message):
        return None  # the real router decides anything about people/marks or with risky wording
    if on_page and DEICTIC.search(message) and 2 <= len(words) <= 30:
        return Route(Intent.platform_faq, message.strip(), shortcut=True)  # the service anchors it to the open page
    standalone = (4 <= len(words) <= 40 and bool(_QSTART.match(message) or "?" in message) and not _ANAPHORA.search(message)
                  and bool(set(words) & _FAQ_WORDS))
    if standalone:
        return Route(Intent.platform_faq, message.strip(), shortcut=True)
    evidence = bool(_ANAPHORA.search(message) or set(words) & _FAQ_WORDS or _CONTINUATION.match(message))
    min_words = 1 if _CONTINUATION.match(message) else 2  # a bare "why?" is a classic follow-up
    if has_history and last_user and evidence and min_words <= len(words) <= 30:
        # Follow-up ("what if the number never changes?"): the free rewrite is previous question + this message.
        # Retrieval, the confidence gate and the grounding guard still protect the answer; no model call needed.
        return Route(Intent.platform_faq, f"{last_user.strip()} {message.strip()}"[:300], shortcut=True)
    return None


# Questions about ORDER ("what comes after X", "which lesson is first", "in what order"). Narrow on purpose: "the first step to connect
# the kit" is not about lesson order. These get an extra search aimed at the Learning Path and the previous/next sections.
SEQUENCE_Q = re.compile(
    r"\b(?:what|which)\b[^?.!]*\b(?:comes?|goes?|is|are|do i do|should i do)\b[^?.!]*\b(?:after|before|next)\b"
    r"|\b(?:next|previous|first|last|final) (?:lesson|module|activity|course|project)\b"
    r"|\b(?:lesson|module|course) (?:order|sequence)\b|\b(?:what|which|in what|in which) order\b|\bsequence of\b"
    r"|\b(?:after|before) (?:the )?[\w' -]{2,40}(?:lesson|module|activity)\b", re.I)
