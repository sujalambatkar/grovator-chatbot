"""Load, validate and cross-check the file-based knowledge base.

Layout (relative to the knowledge directory):
    applications/<slug>/application.yaml     one application per folder (+ optional docs/*.md)
    sensors/<id>.yaml   blocks/<id>.yaml   kits/<id>.yaml   media/<id>.yaml (+ the image file)
"""
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from knowledge.models import Application, Block, Kit, Media, Sensor

KNOWLEDGE_DIR = Path(__file__).resolve().parent


class KnowledgeError(Exception):
    """Raised with ALL problems found, so a content author can fix them in one go."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class Knowledge:
    root: Path
    applications: dict[str, Application] = field(default_factory=dict)
    sensors: dict[str, Sensor] = field(default_factory=dict)
    blocks: dict[str, Block] = field(default_factory=dict)
    kits: dict[str, Kit] = field(default_factory=dict)
    media: dict[str, Media] = field(default_factory=dict)
    docs: dict[str, str] = field(default_factory=dict)   # "<slug>/docs/<file>.md" -> markdown text
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _read(path: Path, model, into: dict, k: Knowledge, expect_id: str | None = None) -> None:
    rel = path.relative_to(k.root)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        obj = model.model_validate(raw)
    except (yaml.YAMLError, ValidationError, TypeError) as exc:
        k.problems.append(f"{rel}: {exc}")
        return
    if expect_id is not None and obj.id != expect_id:
        k.problems.append(f"{rel}: id '{obj.id}' must match the {'folder' if path.name == 'application.yaml' else 'file'} name '{expect_id}'")
    if obj.id in into:
        k.problems.append(f"{rel}: duplicate id '{obj.id}'")
    into[obj.id] = obj


def load(root: Path | None = None) -> Knowledge:
    """Parse everything. Never raises for content errors: look at `.problems` (validate() raises)."""
    k = Knowledge(root=Path(root or KNOWLEDGE_DIR))
    for p in sorted((k.root / "applications").glob("*/application.yaml")):
        _read(p, Application, k.applications, k, expect_id=p.parent.name)
    for p in sorted((k.root / "sensors").glob("*.yaml")):
        _read(p, Sensor, k.sensors, k, expect_id=p.stem)
    for p in sorted((k.root / "blocks").glob("*.yaml")):
        _read(p, Block, k.blocks, k, expect_id=p.stem)
    for p in sorted((k.root / "kits").glob("*.yaml")):
        _read(p, Kit, k.kits, k, expect_id=p.stem)
    for p in sorted((k.root / "media").glob("*.yaml")):
        _read(p, Media, k.media, k, expect_id=p.stem)
    for p in sorted((k.root / "applications").glob("*/docs/*.md")):
        k.docs[str(p.relative_to(k.root / "applications"))] = p.read_text(encoding="utf-8")
    _cross_check(k)
    return k


def _cross_check(k: Knowledge) -> None:
    """References between files must resolve; code listings must at least parse as Python."""
    for a in k.applications.values():
        for ref in a.components:
            if ref not in k.sensors and ref not in k.kits and not ref.startswith("other:"):
                k.problems.append(f"application {a.id}: component '{ref}' is not a known sensor or kit (use 'other:<name>' for plain parts)")
        if a.circuit.kit not in k.kits:
            k.problems.append(f"application {a.id}: circuit.kit '{a.circuit.kit}' not found in kits/")
        if a.circuit.sensor not in k.sensors:
            k.problems.append(f"application {a.id}: circuit.sensor '{a.circuit.sensor}' not found in sensors/")
        if a.circuit.image and a.circuit.image not in k.media:
            k.problems.append(f"application {a.id}: circuit.image '{a.circuit.image}' not found in media/")
        for b in a.block_stack:
            if b not in k.blocks:
                k.problems.append(f"application {a.id}: block_stack entry '{b}' not found in blocks/")
        _parses(f"application {a.id} program.code", a.program.code, k)
        nums = [s.n for s in a.steps]
        if nums != list(range(1, len(nums) + 1)):
            k.problems.append(f"application {a.id}: steps must be numbered 1..n in order, got {nums}")
    for s in k.sensors.values():
        for b in s.blocks:
            if b not in k.blocks:
                k.problems.append(f"sensor {s.id}: block '{b}' not found in blocks/")
        if s.interface == "I2C" and not s.i2c:
            k.problems.append(f"sensor {s.id}: interface is I2C but there is no i2c: section")
        if s.interface == "UART" and not s.uart:
            k.problems.append(f"sensor {s.id}: interface is UART but there is no uart: section")
    for b in k.blocks.values():
        if b.sensor and b.sensor not in k.sensors:
            k.problems.append(f"block {b.id}: sensor '{b.sensor}' not found in sensors/")
        if b.language == "python":
            _parses(f"block {b.id} code", b.code, k)
    for kit in k.kits.values():
        for g in kit.port_groups:
            if g.photo and g.photo not in k.media:
                k.problems.append(f"kit {kit.id}: port group '{g.name}' photo '{g.photo}' not found in media/")
    for m in k.media.values():
        if not (k.root / "media" / m.file).is_file():
            k.problems.append(f"media {m.id}: file '{m.file}' is missing from media/")
        if not m.verified:
            k.warnings.append(f"media {m.id} is verified: false (shown to students only with its caption, pins from it are not trusted)")
        for kind, ids in m.related.items():
            table = {"application": k.applications, "sensor": k.sensors, "kit": k.kits}.get(kind)
            if table is None:
                k.problems.append(f"media {m.id}: related key '{kind}' must be application, sensor or kit")
            else:
                k.problems += [f"media {m.id}: related {kind} '{i}' not found" for i in ids if i not in table]


def _parses(label: str, code: str, k: Knowledge) -> None:
    import ast

    try:
        ast.parse(code)
    except SyntaxError as exc:
        k.problems.append(f"{label}: is not valid Python ({exc.msg}, line {exc.lineno})")


def validate(root: Path | None = None) -> Knowledge:
    k = load(root)
    if k.problems:
        raise KnowledgeError(k.problems)
    return k


def superseded_gateway_devices(root: Path | None = None) -> set[str]:
    """Keys of generic gateway-dataset devices that a sensor file replaces (cheap: reads only `supersedes:` of sensors/*.yaml)."""
    out: set[str] = set()
    for p in sorted((Path(root or KNOWLEDGE_DIR) / "sensors").glob("*.yaml")):
        try:
            out |= set((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("supersedes") or [])
        except yaml.YAMLError:
            pass
    return out
