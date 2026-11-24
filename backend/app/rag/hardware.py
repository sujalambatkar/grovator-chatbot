"""Turns the structured gateway dataset (JSON) into knowledge documents, DYNAMICALLY.

Nothing here knows about a particular sensor: every document is rendered from the records in the JSON file, so
editing or extending the file changes what the chatbot knows. What this module adds on top of "flatten the JSON":

* relationships: a device page carries the gateway interface it connects to (pin list, connector pins, rules), and an
  interface page lists the devices that use it;
* generated wiring steps, built from each device's own fields (pins, signal, connect_to, warning);
* an explicit "not documented" section, so the model has the dataset's gaps in front of it instead of filling them;
* an alias index (names a student might type -> device key) used to keep track of "the sensor we are talking about".
"""
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ALL_ROLES = ["student", "teacher", "admin", "superadmin"]
_ACRONYMS = {"ir", "ldr", "lcd", "i2c", "dc", "led", "uart", "gpio", "usb", "esp32", "dht11", "io", "iot"}
_GENERIC = {"sensor", "module", "device", "motor", "i2c", "uart", "the", "and", "unit", "detection"}
_INTERFACE_TOKENS = {  # words that tie a wiring rule to one interface, so a device page lists only the rules that apply
    "digital": "digital", "i2c": "i2c", "uart": "uart", "analog": "analog", "keypad": "dedicated", "motor": "dedicated", "stepper": "dedicated", "usb": "dedicated",
}


@dataclass
class HwDoc:
    source: str
    title: str
    category: str
    order: int
    body: str
    role_visibility: list[str] = field(default_factory=lambda: list(ALL_ROLES))


def pretty(key: str) -> str:
    return " ".join(w.upper() if (w in _ACRONYMS or any(c.isdigit() for c in w)) else w.capitalize() for w in key.replace("-", "_").split("_"))


def _fmt(v) -> str:
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_fmt(x)}" for k, x in v.items())
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class Gateway:
    """The parsed dataset plus the lookups the renderers and the conversation logic need."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.product = data.get("product", {})
        self.interfaces: dict = data.get("interfaces", {})
        self.devices: dict = data.get("devices", {})
        self.rules: list[str] = data.get("wiring_rules", [])
        self.concepts: dict = data.get("concepts", {})
        self.name = self.product.get("name", "Gateway")
        self._aliases = self._build_aliases()

    # ------------------------------------------------------------------ relationships
    def interface_key(self, dev_key: str, dev: dict) -> str | None:
        """Which interface record describes the gateway side of this device (None if the dataset has no such record)."""
        if dev_key in self.interfaces:
            return dev_key
        iface = _norm(str(dev.get("interface", "")))
        if not iface:
            return None
        first = iface.split()[0]
        for k in self.interfaces:
            nk = _norm(k)
            if nk == iface or nk.split()[0] == first or nk == f"{first} gpio":
                return k
        return None

    def devices_on(self, iface_key: str) -> list[str]:
        return [k for k, d in self.devices.items() if self.interface_key(k, d) == iface_key]

    def concept_for(self, dev_key: str) -> str | None:
        for ck in (dev_key, dev_key.removesuffix("_module"), dev_key.removesuffix("_sensor")):
            if ck in self.concepts:
                return self.concepts[ck]
        return None

    # ------------------------------------------------------------------ aliases (what a student might call a device)
    def _build_aliases(self) -> list[tuple[re.Pattern, str]]:
        token_owners: dict[str, set[str]] = {}
        for k in self.devices:
            for t in k.split("_"):
                token_owners.setdefault(t, set()).add(k)
        out: dict[str, str] = {}
        for k in self.devices:
            spaced = k.replace("_", " ")
            cands = {spaced, _norm(pretty(k))}
            for suffix in (" sensor", " module"):
                if spaced.endswith(suffix) and len(spaced) - len(suffix) >= 3:
                    cands.add(spaced[: -len(suffix)])
            for t in k.split("_"):
                if len(t) >= 3 and t not in _GENERIC and token_owners[t] == {k}:
                    cands.add(t)
            for c in cands:
                out.setdefault(c, k)
        # longest first, so "dc motor" wins over "motor"; whole-word, optional plural s
        return [(re.compile(rf"(?<![a-z0-9]){re.escape(a)}s?(?![a-z0-9])", re.I), k)
                for a, k in sorted(out.items(), key=lambda x: -len(x[0]))]

    def detect(self, text: str) -> list[str]:
        """Device keys named in `text`, in order of appearance (each once)."""
        text = _norm(text)
        found: list[tuple[int, str]] = []
        taken: list[tuple[int, int]] = []
        for pat, key in self._aliases:
            m = pat.search(text)
            if m and not any(s < m.end() and m.start() < e for s, e in taken) and key not in [k for _, k in found]:
                found.append((m.start(), key))
                taken.append((m.start(), m.end()))
        return [k for _, k in sorted(found)]

    def label(self, key: str) -> str:
        return pretty(key)

    def examples(self, n: int = 5) -> list[str]:
        keys = [k for k in self.devices if k not in ("keypad", "camera")]
        step = max(1, len(keys) // n)
        return [pretty(k) for k in keys[::step][:n]]

    # ------------------------------------------------------------------ rendering
    def rules_for(self, iface_group: str | None) -> list[str]:
        out = []
        for r in self.rules:
            tags = {g for w, g in _INTERFACE_TOKENS.items() if re.search(rf"\b{w}\b", r, re.I)}
            if not tags or (iface_group and iface_group in tags):
                out.append(r)
        return out

    def _group(self, dev_key: str, dev: dict, iface_key: str | None) -> str | None:
        probe = _norm(f"{dev.get('interface', '')} {iface_key or ''} {dev_key}")
        for w, g in _INTERFACE_TOKENS.items():
            if re.search(rf"\b{w}\b", probe):
                return g
        return None

    def _iface_lines(self, key: str) -> list[str]:
        def label(k: str) -> str:
            if k.isupper():
                return k
            text = " ".join(w.upper() if w in _ACRONYMS else w for w in k.split("_"))
            return text[0].upper() + text[1:]
        return [f"- {label(k)}: {_fmt(v).rstrip('.')}." for k, v in self.interfaces[key].items()]

    def render_interface(self, key: str) -> str:
        lines = [f"# {pretty(key)} on the gateway", *self._iface_lines(key)]
        users = self.devices_on(key)
        if users:
            lines += ["", f"# Devices that connect through {pretty(key)}", ", ".join(pretty(u) for u in users) + "."]
        return "\n".join(lines)

    def device_steps(self, key: str) -> list[str]:
        """Wiring steps generated from this device's own fields (used by the guide AND by the pre-written answers)."""
        dev = self.devices[key]
        ik = self.interface_key(key, dev)
        digital = ik == "digital_gpio" or "digital" in _norm(str(dev.get("interface", "")))
        steps: list[str] = []
        if "pins" in dev:
            for p, t in dev["pins"].items():
                if t.lower().startswith("verify"):
                    steps.append(f"{p}: check the module's voltage first (record says \"{t}\"; no value given).")
                elif "digital gpio" in t.lower():
                    steps.append(f"{p} to the Signal pin of a free digital GPIO connector.")
                else:
                    steps.append(f"{p} to {t}.")
        elif "signal" in dev:
            steps.append(f"{dev['signal']} to " + ("the Signal pin of a free digital GPIO connector." if digital else f"{dev.get('connect_to', 'the gateway')}."))
            conn = _fmt(self.interfaces[ik].get("connector", [])) if ik and "connector" in self.interfaces[ik] else ""
            steps.append((f"Power and ground: the connector provides {conn}; " if conn else "")
                         + "the module's own power pins and voltage are not documented (check its labels and datasheet).")
        elif "connect_to" in dev:
            steps.append(f"Plug it into the {dev['connect_to']}.")
        if "warning" in dev:
            steps.append(f"Before powering: {dev['warning']}")
        return steps

    def render_device(self, key: str) -> str:
        """One guide per device. Headings do not repeat the device name: every chunk is already prefixed with the title.
        Each section is written to be retrieved ON ITS OWN (a direct lookup reads only the 1-2 sections a question needs)."""
        dev = self.devices[key]
        name = pretty(key)
        ik = self.interface_key(key, dev)
        digital = ik == "digital_gpio" or "digital" in _norm(str(dev.get("interface", "")))

        # the record itself, compact (every field of the JSON, verbatim)
        rec = [f"interface {dev.get('interface', 'not stated')}"]
        if "signal" in dev:
            rec.append(f"signal pin {dev['signal']}")
        if "connect_to" in dev:
            rec.append(f"connects to {dev['connect_to']}")
        if "pins" in dev:
            rec.append("pins " + ", ".join(f"{p} to {t}" for p, t in dev["pins"].items()))
        lines = ["# How to wire", "Record: " + "; ".join(rec) + "."]

        steps = self.device_steps(key)
        lines += [f"{i}. {st}" for i, st in enumerate(steps, 1)]
        if ik:  # the gateway side of the connection (relationship to the interface record), one line
            lines.append(f"Gateway {pretty(ik)}: " + "; ".join(l[2:].rstrip(".") for l in self._iface_lines(ik)) + ".")

        target = dev.get("connect_to") or (_fmt(self.interfaces[ik].get("connector", "")) if ik and "connector" in self.interfaces[ik] else "")
        lines += ["", "# Components needed",
                  f"The {name} module and the {self.name}" + (f" ({target})." if target else ".")
                  + " No cables, power supplies or other parts are listed: do not add any."]

        lines += ["", "# Safety checks before powering", *(f"- {r}" for r in self.rules_for(self._group(key, dev, ik)))]

        concept = self.concept_for(key)
        if concept:
            lines += ["", "# About", concept]
        lines += ["", "# Wired to the wrong pins",
                  "Not described in the dataset; do not predict outcomes. Documented risk: 5V signals may not be safe for ESP32 GPIOs "
                  "(use level shifting). Re-check the record and the labels printed on the gateway."]
        lines += ["", "# After wiring (next steps)",
                  "Not in the dataset: software or IDE setup, example code or APIs, expected readings, module-specific troubleshooting. "
                  "Do not invent them; suggest the module datasheet or the teacher."]
        lines += ["", "# Troubleshooting (documented checks only)",
                  "1. Wiring follows the labels printed on the gateway. 2. Each pin matches the record. "
                  "3. Module voltage, signal level, current draw and pinout suit the gateway."
                  + (" 4. GPIO32 is used as digital OR analog, not both." if digital else "")
                  + " Beyond these the dataset has no diagnosis: check the datasheet or ask the teacher."]
        return "\n".join(lines)

    def render_overview(self) -> str:
        p = self.product
        lines = [f"# About the {self.name}", f"{self.name}: {p.get('purpose', '')}", "Scope: " + _fmt(p.get("scope", [])) + ".",
                 "", "# Interfaces on the gateway"]
        for k, rec in self.interfaces.items():
            users = self.devices_on(k)
            lines.append(f"- {pretty(k)}" + (f": used by {', '.join(pretty(u) for u in users)}" if users else "") + ".")
        lines += ["", "# Sensors, actuators and modules the gateway documents", ", ".join(pretty(k) for k in self.devices) + "."]
        return "\n".join(lines)

    def render_rules(self) -> str:
        return "# Wiring rules\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(self.rules, 1))

    def render_concepts(self) -> str:
        return "\n\n".join(f"# {pretty(k)}\n{v}" for k, v in self.concepts.items())

    # ------------------------------------------------------------------ documents
    def docs(self) -> list[HwDoc]:
        out = [HwDoc("gateway/overview", f"{self.name}: Overview", "gateway", 1, self.render_overview())]
        if self.rules:
            out.append(HwDoc("gateway/wiring-rules", "Gateway Wiring Rules", "gateway", 2, self.render_rules()))
        for i, k in enumerate(self.interfaces):
            out.append(HwDoc(f"interfaces/{k}", f"Gateway Interface: {pretty(k)}", "interfaces", 10 + i, self.render_interface(k)))
        for i, k in enumerate(self.devices):
            out.append(HwDoc(f"devices/{k}", f"{pretty(k)}: Connection Guide", "devices", 100 + i, self.render_device(k)))
        if self.concepts:
            out.append(HwDoc("concepts/glossary", "Electronics Concepts", "concepts", 300, self.render_concepts()))
        return out

    # ------------------------------------------------------------------ what the dataset does not say (for the report/tests)
    def gaps(self) -> list[str]:
        g = []
        if not any("kit" in k.lower() for k in self.data):
            g.append("No separate kit records: the only product is the gateway, so there are no kit-to-kit comparisons or kit contents.")
        for k, d in self.devices.items():
            if self.interface_key(k, d) is None:
                g.append(f"{k}: interface '{d.get('interface')}' has no interface record")
        return g


@lru_cache(maxsize=4)
def load_gateway(path: str) -> Gateway:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    from knowledge.loader import superseded_gateway_devices  # sensors with their own, better records replace the generic entry

    gone = superseded_gateway_devices()
    data["devices"] = {k: v for k, v in data.get("devices", {}).items() if k not in gone}
    return Gateway(data)


def gateway_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.json")) if root.exists() else []


def load_hw_docs(root: Path) -> list[HwDoc]:
    return [d for p in gateway_files(root) for d in load_gateway(str(p)).docs()]


class _Empty:
    devices: dict = {}

    def detect(self, text: str) -> list[str]:
        return []

    def label(self, key: str) -> str:
        return key

    def examples(self, n: int = 5) -> list[str]:
        return []


@lru_cache(maxsize=4)
def hardware_index(root: str):
    """The gateway of this knowledge dir (the first JSON file), or an inert stand-in when there is none."""
    files = gateway_files(Path(root))
    return load_gateway(str(files[0])) if files else _Empty()
