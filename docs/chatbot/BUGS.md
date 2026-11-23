# Bugs Diary

_No bugs yet._

## Phase 2
1. **Answer truncated at max_tokens (found live).** gpt-oss spends hidden reasoning tokens inside `max_tokens`; one multi-turn answer was cut off. Fix: explicit length limit in the system prompt, `truncated` flag from `finish_reason`. Verified 0/36 truncated afterwards.
2. **Follow-up lost context when the router was rate limited (found live).** Fallback searched on the raw follow-up and missed the right doc. Fix: prepend previous user message (D19), unit-tested.
3. **Vacuous test assertion** (`... or True`) in the window test, caught in self-review and replaced with a real check.
4. **Groq free tier is 8,000 tokens/minute per model** (not a code bug; found when repeated live runs got 429s). See Phase 5 notes.

## Phase 3
1. **Third-person family details bypassed the sensitive filter.** Rule only matched "my mother"; extractor writes "Student's mother...". Found by unit test; rule now covers both persons.
2. **Extractor trigger too broad.** Bare "my" fired it on "Where are my saved projects?". Narrowed to self-statements; asserted by a test (0 extractor calls for plain questions).
3. **Second-resolution timestamps** made LRU eviction and list order arbitrary. Now microseconds.
4. **Method named `list` shadowed the builtin** in `MemoryStore` (broke `list[str]` annotations). Renamed `list_all`.
5. **Extractor stored transient tasks as durable goals** ("User wants to read temperature"), seen on live Groq. Prompt tightened; rerun stored only durable facts.
6. **Router false positive (open):** "I have asthma and my mother is a doctor. How do I run my program?" is routed `off_topic`, so the user's valid question is refused. Safe-side failure, not a leak. Revisit with Phase 4 input handling (strip/redact personal details, route on the remaining question).

## Phase 4
1. **Streamed tokens leaked before the output guard could act** (found by the red-team suite: vendor names, system-prompt text, invented marks, ids all reached the wire before a `replace` event). Fix: verify-then-stream (D31). Re-run: 0 leaks in 10 compromised-model scenarios; asserted over every SSE frame.
2. **Rate limiter ignored `create_app(settings=...)`** (it read the global cached settings). Now reads `app.state.settings`. Found because per-app limits in tests had no effect.
3. **Cross-tenant phrasing in reverse word order slipped past L1** ("marks of students in another school", "all students from every organization"). Added reverse patterns; the tool layer would have denied them anyway, but L1 saves the tokens.
4. **ID redaction skipped 2-character ids** (min length 3). Now 2.
5. **"Show my report" routed to FAQ** (live): student got "I don't have that information". Router prompt widened (D38). My own live control had marked this "OK" because it only checked `grounded`; controls now require the expected data substring.
6. **Phase 3 carry-over fixed:** messages with incidental personal details ("I have asthma and my mother is a doctor. How do I run my program?") were refused off_topic. Now answered (live control `ctl-p1`).
7. **Distress message answered chattily** by the router (live). Now an L1 rule + fixed supportive reply (D37).
8. **Test-only issues:** per-IP counter also counts rejected requests, so a per-user test must use a high IP limit; canary `meta` collided with the SSE frame name `event: meta`.

### Known limitations (Phase 4)
- Spaced-letter evasion ("i g n o r e") and masked profanity ("f***") are not caught by L1; they rely on the router / output guard. Covered live (router) but not deterministically.
- Health/family/name filters are keyword rules, not a classifier.
- Lexical grounding is a heuristic (margin 0.12 over the lowest real answer seen); expect occasional safe false refusals.
- The mock data and tool shapes are assumptions about Grovator's schema; the real adapter must be written against the real API/DB.

## Phase 5
1. **False refusal in the baseline benchmark** (turn "and how do I make it follow a line?"): the Phase 4 numeric guard rejected a real answer because it contained a number > 10 absent from the context. Rule narrowed to performance claims (D46); the strict rule for tool data is unchanged.
2. **Live scripts imported `tests.conftest`, which now forces the optimisation flags OFF**, so an "after" benchmark would silently have measured the baseline. Scripts use `scripts/common.py`; the benchmark prints the flags it ran with.
3. **Compression threshold too low**: an unrelated chunk survived at 0.5 because bge scores unrelated sentences 0.47-0.56. Raised to 0.6 (relevant sentences >= 0.7).
4. **Window 0 compression lost a key fact** (the worked example beside the best sentence). Added the sentence window (D44).
5. **Test gaps found by mutation testing**: no test exercised "data word beats platform word" in the shortcut (mutant survived, now killed). One surviving mutant is equivalent: `cacheable()` already rejects redaction placeholders, so the extra `out.action == "pass"` guard is defence in depth.
6. Test-design errors fixed: a cache test assumed a canned reply was grounded for an unrelated question (the guard rightly blocked it); a usage test compared a message with extra text against a cached plain question; a vacuous `or True` assertion was removed.
7. **Follow-up cache served a wrong-but-plausible answer** (benchmark turn "I forgot it" -> generic login steps). Follow-up caching disabled by default (D48); test covers both modes.
8. **"Ungrounded" refusal of a real answer after lazy summary** (benchmark turn "can I get a deleted one back?", reproduced 3/3 at support 0.44). Not compression (same score vs full context): the Phase 4 baseline only passed because summary text padded the grounding source. Threshold recalibrated on real data (D47) with a retrieval-based regression test.

## Phase 6
1. **Feedback reasons were silently dropped** (found by the browser E2E, not unit tests). The UI saves 👎 first and the reason second; the backend treated the second call as "same rating, nothing to do". Fixed with per-request reason bookkeeping; regression tests for set / change / retract.
2. **On phones the floating button covered the Send button** (found by looking at the screenshot; every assertion had passed). The launcher is hidden when the full-screen panel is open; E2E now taps Send on a phone viewport.
3. **Auth failure unmounted the widget with no explanation.** The demo host now shows a "session expired" message; the widget reports the error through `onAuthError`.
4. **`Source:` listed unrelated documents** (every retrieved doc). Now only documents the answer draws on (D57). First two thresholds I tried were wrong (too exclusive / too inclusive); the final one was chosen from measured overlap scores.
5. **Numeric guard refused a correct tutorial answer** (live eval: a variable named `score`, numbers in a compression-dropped sentence). Now checks the full retrieved text (D58).
6. **Eval harness had three gaps found by mutating the code under test**: LLM-call counting was cumulative (one leak failed every later item); vendor/prompt-leak/number rules were never tested in isolation (every sample was also caught by the grounding check); retrieval quality tests did not test knowledge access control. Added per-item counting, isolated cases, and a `knowledge_rbac` suite (role, org-only and school-only content). All mutants are now caught.
7. **Config hardening**: default JWT secret and tokens without `exp` were accepted. Production startup checks and required `exp`/`sub` added (D56).
8. Tooling: Node 18 `fetch("localhost")` prefers IPv6 while uvicorn binds IPv4 (E2E readiness probe); port 8000 was held by another Docker process (E2E uses 8765).

### Known limitations (Phase 6)
- Widget verified in headless Chromium only (desktop light/dark, phone viewport), not on real devices, Safari or Firefox. No automated accessibility audit (axe); labels/roles/focus were checked by hand and by a few assertions.
- The widget is a copy-in component, not a published package; integrating with Grovator's real frontend and auth is untested because that code was not available.
- The full 66-turn live eval was not re-run end-to-end after the final numeric-guard fix (affected suites were: 20/20).

## Phase 7 (demo-first rework)
1. **Refusal responses lacked token fields** (found when the measured comparison crashed on the off-topic turn). Every response now carries `tokens_total` and `naive_tokens`.
2. **Page-aware cache never matched**: lookup embedded the raw message but the store keyed on the page-prefixed query. Both use the same text now.
3. **My first "naive bot" estimate was dishonest.** For a 258-token page, "send the page" is CHEAPER than this project on turn 1 (measured 816 vs 1,018). The baseline is now the whole site, and the comparison script also reports the page-only bot, measured.
4. **Per-turn cost was too high** (1,000 to 1,800 tokens on follow-ups): router 380-500 + LLM summary ~380. Fixed by free follow-up routing and the extractive summary: 6,201 to 3,539 on the same 5 turns, then 3,677 on the 7-turn comparison against 7,503 for the page-only bot.
5. **Free follow-up rewrite lost topic shifts** ("and how do I log in again?" never retrieved the login page). My first regression test had a false premise (retrieval already found the loops page); probing real pairs found the actual failures. Fix: search the new message alone too, at 2x weight, plus multi-vector compression. Tests pin BOTH directions (topic shift keeps its docs; a context-dependent follow-up keeps its context) and were mutation-checked.
6. **"if" counted as a platform word**, so "what if the number never changes?" lost its context. Vocabulary restricted to platform-specific words.
7. **Live multi-turn red team: a poem was written** after the router was skipped for follow-ups (grounding passed because the poem reused lesson words). Added the off-topic-task rule (D67). Re-run: 43/43.
8. **Eval harness**: refusal detection looked only at `grounded`, misreading a polite model refusal as compliance; a canary matched a name the attacker typed. Both fixed.
9. A stale expectation in four tests assumed all student documents lived under `student/`; replaced by a check derived from each file's `role_visibility`.

### Known limitations (Phase 7)
- The token comparison is one 7-message conversation, one run each, on a 29-page synthetic site; the whole-site figure is an estimate. The page-only baseline is measured.
- Router-free follow-ups trade some safety-net for cost; the deterministic rules, retrieval gate, grounding guard and prompt cover the cases tested (43/43 live attacks incl. 15 multi-turn), but semantic off-topic requests that avoid the listed words rely on the model declining.
- The free follow-up rewrite concatenates the previous question; unusual topic switches can still retrieve extra context (harmless, costs a few tokens).

## Phase 8 (reported from the UI)
1. **"Hello how are you" re-answered the previous question.** The free follow-up rewrite treated ANY message after a question as a follow-up. Now needs evidence (pronoun, platform word, or continuation cue); obvious chit-chat gets a free canned reply (whole-message match only). Tests written first and mutation-checked; live multi-turn eval case added.
2. **"I love pizza!"**: small talk classified by the router was free-written by the small model, which invented a personality. A model never writes small talk now: a fixed redirect is returned. (My first fix also forgot to set `shortcut=True` on the canned routes, caught by a test.)
3. **"What is the next lesson after LED and Buzzer?" had no answer**: the lesson order existed nowhere in the content. Added the Learning Path page and Previous/Next sections (D70).
4. **"After move and turn" retrieved the move-and-turn lesson, not the sequence**; fixed with an order-question search (D71).
5. **Order list truncated with "(and so on...)"** found by reading the live answer, not by a test: sentence compression keeps only the best sentences of a list. Order questions are no longer compressed.
6. My eval check for the chit-chat case banned the word "block", which the canned reply legitimately contains; it now checks for the leaked summary instead.

## Phase 9 (real gateway dataset, found by live Groq runs)
1. **"no GPIO pin is used twice" became "no GPIO [CREDENTIAL] used twice"**: `pin is ...` matched the PIN-code rule. Narrowed to PIN/OTP followed by 3-8 digits (regression test in `test_hardware.py`; written after the live failure, mutation-checked only for the other new rules).
2. **"Which sensors can I connect?" after an ultrasonic chat listed only the ultrasonic sensor**: the sensor name was added to every follow-up search. Only continuations get it now. A list question was then still answered with 3 sensors because only 3 device pages were retrieved: list questions get an extra overview search.
3. **The model invented jumper wires, a power source and short-circuit outcomes** ("components needed", "what if wrong pins"): the device guides now state explicitly that the dataset lists no such things.
4. **"Can I use GPIO21?" could not say what GPIO21 is for** (the I2C page was not retrieved): a GPIO named by the student is now also searched on its own.
5. A made-up pin was blocked once in a live run (before the fix of 4) with the generic safe reply; the answer is deliberately conservative.

### Known limitations (Phase 9)
- The model sometimes adds general electronics knowledge that is true but not in the records (e.g. "ESP32 GPIOs are 3.3 V tolerant"); the pin guard and lexical grounding do not catch non-pin facts.
- The VCC of an I2C module: the records say "verify module voltage" while the gateway I2C connector lists 3.3V; the model may combine them. Not wrong per the records, but not stated either.
- No software, code, expected output, fault diagnosis, cables, wiring diagrams or separate kits exist in the dataset; "what next" and troubleshooting answers can only repeat the documented safety checks and refer to the datasheet or teacher.
- Dataset oddities (reported, not "fixed"): `led_module.signal` is `DO` (D0?), `keypad`/`dc_motor` appear as both interface and device, `water_pump` (motor) and `camera` (usb) have no interface record, motor terminals `M14/M15` look unusual, GPIO32 is listed under both digital and analog, `ldr_module` is typed digital though its concept says it may be analog.
- Compression and grounding thresholds were calibrated on the old synthetic corpus.

