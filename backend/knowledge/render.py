"""Turn validated knowledge into index sections.

One section = one chunk. A section is NEVER split: code listings, pinouts and tables stay whole, because half a pinout is a
wrong pinout. Every section carries metadata (type, application, sensor, topic) for the index.
"""
import re
from dataclasses import dataclass, field

from knowledge.loader import Knowledge


@dataclass
class Section:
    source: str          # stable id, e.g. "applications/<slug>/circuit"
    title: str           # what the student sees as the source, e.g. "application.yaml > Circuit"
    text: str            # the body (title and heading are prefixed by the indexer)
    meta: dict = field(default_factory=dict)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def connection_rows(k: Knowledge, app_id: str) -> list[dict]:
    a = k.applications[app_id]
    rows = [{"sensor_pin": c.sensor_pin, "kit_pin": c.kit_pin, "note": c.note, "verified": c.verified} for c in a.circuit.connections]
    return rows


def render_all(k: Knowledge) -> list[Section]:
    out: list[Section] = []
    for a in k.applications.values():
        base = f"applications/{a.id}"
        sensor = a.circuit.sensor
        meta = {"application": a.id, "sensor": sensor}
        kit = k.kits[a.circuit.kit]
        sec = lambda slug, heading, body, topic, **m: out.append(Section(  # noqa: E731
            f"{base}/{slug}", f"{a.title} > {heading}", body, {**meta, "type": "application", "topic": topic, **m}))

        has_docs = any(d.startswith(f"{a.id}/docs/") for d in k.docs)
        overview = [f"{a.summary}", f"Input: {a.iot_blocks.input}.", f"Processor: {a.iot_blocks.processor}.",
                    f"Output: {a.iot_blocks.output}.", f"Notification: {a.iot_blocks.notification}.",
                    "Parts: " + ", ".join(_component_name(k, c) for c in a.components) + "."]
        if not has_docs:
            overview += [f"Challenge: {a.challenge}", "Objectives:\n" + _bullets(a.objectives)]
        sec("overview", "Overview", "\n".join(overview), "overview")
        sec("steps", "The 5 step process", "\n".join(f"{s.n}. {s.title}: {s.text}" for s in a.steps), "steps")
        sec("logic", "Application logic and thresholds",
            "Logic:\n" + _bullets(a.logic) + "\nThresholds used by the lesson program:\n"
            + _bullets([f"{t.name} ({t.quantity}): {t.rule} -> {t.result}" for t in a.thresholds]), "thresholds")
        table = "\n".join(f"{c.sensor_pin} -> {c.kit_pin}" + (f" ({c.note})" if c.note else "") for c in a.circuit.connections)
        leds = "\n".join(f"{l.name} on GPIO{l.gpio}: {l.meaning}" for l in a.circuit.leds)
        sec("circuit", "Circuit: wiring and pins",
            f"The pulse oximeter ({k.sensors[sensor].name}) connects to the {kit.name} over {k.sensors[sensor].interface}. "
            f"Connection table (sensor pin -> kit pin):\n{table}\nLEDs (from the program):\n{leds}\n" + _bullets(a.circuit.notes)
            + "\nNot documented yet: " + "; ".join(kit.not_documented) + ".", "wiring")
        sec("program", "Program (MicroPython)", f"{a.program.description}\n" + _bullets(a.program.code_notes) + "\n" + a.program.code, "code")
        for i, e in enumerate(a.program.explanations, 1):
            sec(f"line-by-line-{i}", f"Program line by line, part {i}", f"Statements: {e.code}\nMeaning:\n" + _bullets(e.meaning), "explain-code")
        if not has_docs:
            sec("science", "How it works", "\n".join(a.science), "science")
        for i, f in enumerate(a.faq, 1):
            sec(f"faq-{i}", f"FAQ: {f.q}", f"Question: {f.q}\nAnswer: {f.a}", "faq", triggers=f.triggers)
        sec("extension", "Extension questions and resources",
            "Extension questions:\n" + _bullets(a.extension_questions) + "\nSuggested resources:\n" + _bullets(a.resources), "extension")
        if a.safety_note:
            sec("safety", "Safety note", a.safety_note, "safety")

    for s in k.sensors.values():
        pins = "\n".join(f"{p.pin} -> {p.connects_to}" + (f" ({p.note})" if p.note else "") for p in s.pins)
        iface = s.interface
        if s.i2c:
            iface += f": address {s.i2c.address}, SCL {s.i2c.scl}, SDA {s.i2c.sda}" + (f", {s.i2c.freq} Hz" if s.i2c.freq else "")
        if s.uart:
            iface += f": UART{s.uart.uart_id}, {s.uart.baudrate} baud, TX GPIO{s.uart.tx_gpio}, RX GPIO{s.uart.rx_gpio} (in the code)"
        body = (f"{s.description}\nAlso called: {', '.join(s.aliases)}.\nType: {s.sensor_type}. Device type: {s.device_type}. Modules: {', '.join(s.modules)}.\n"
                f"Interface: {iface}\nPins (sensor pin -> kit):\n{pins}\nVariables: {', '.join(s.variables)}\n"
                f"Blocks, in order: {', '.join(k.blocks[b].title for b in s.blocks)}\n" + _bullets(s.notes))
        out.append(Section(f"sensors/{s.id}", f"{s.name} > Sensor details", body,
                           {"type": "sensor", "sensor": s.id, "topic": "sensor", "verified": s.pins_verified}))
        if s.how_it_measures:
            out.append(Section(f"sensors/{s.id}/measure", f"{s.name} > How the block code measures",
                               _bullets(s.how_it_measures), {"type": "sensor", "sensor": s.id, "topic": "measure"}))
    for b in k.blocks.values():
        body = (f"Block title: {b.title}. Connection: {b.connection}. {b.description}\nVersion: {b.variant}\n"
                + (f"Variables: {', '.join(b.variables)}\n" if b.variables else "") + _bullets(b.code_notes) + f"\nCode:\n{b.code}")
        out.append(Section(f"blocks/{b.id}", f"Block: {b.title}", body,
                           {"type": "block", "sensor": b.sensor or "", "topic": "block", "verified": b.verified}))
    for kit in k.kits.values():
        groups = "\n".join(f"{g.name}: {', '.join(g.pins)}" for g in kit.port_groups)
        body = (f"{kit.description}\n" + (f"Port labels:\n{groups}\n" if groups else "") + "Documented: " + "; ".join(kit.documented_facts)
                + "\nNot documented yet: " + "; ".join(kit.not_documented) + (
                    "\n(Labels from photos, not confirmed.)" if not kit.verified else ""))
        out.append(Section(f"kits/{kit.id}", f"Kit: {kit.name}", body, {"type": "kit", "topic": "kit", "verified": kit.verified}))
    for m in k.media.values():
        out.append(Section(f"media/{m.id}", f"Image: {m.caption}", f"{m.caption}. {m.shows}",
                           {"type": "media", "topic": "media", "verified": m.verified, "file": m.file}))
    for path, md in k.docs.items():
        slug = path.split("/")[0]
        title = re.search(r"^title:\s*(.+)$", md, re.M)
        body = re.sub(r"^---.*?---\s*", "", md, flags=re.S)
        for n, (heading, text) in enumerate(_md_sections(body), 1):
            out.append(Section(f"applications/{path}#{n}", f"{(title.group(1) if title else path)} > {heading}", text,
                               {"type": "application", "application": slug, "topic": "doc"}))
    return out


def _md_sections(md: str) -> list[tuple[str, str]]:
    parts, head, buf = [], "", []
    for line in md.splitlines():
        if line.startswith("#"):
            if buf and "".join(buf).strip():
                parts.append((head, "\n".join(buf).strip()))
            head, buf = line.lstrip("# ").strip(), []
        else:
            buf.append(line)
    if buf and "".join(buf).strip():
        parts.append((head, "\n".join(buf).strip()))
    return parts


def _component_name(k: Knowledge, ref: str) -> str:
    if ref.startswith("other:"):
        return ref[6:]
    return (k.sensors.get(ref) or k.kits[ref]).name
