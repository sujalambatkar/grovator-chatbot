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
        return [f"- {k if k.isupper() else k.replace('_', ' ').capitalize()}: {_fmt(v).rstrip('.')}." for k, v in self.interfaces[key].items()]

    def render_interface(self, key: str) -> str:
        lines = [f"# {pretty(key)} on the gateway", *self._iface_lines(key)]
        users = self.devices_on(key)
        if users:
            lines += ["", f"# Devices that connect through {pretty(key)}", ", ".join(pretty(u) for u in users) + "."]
        return "\n".join(lines)

    def _digital_hint(self) -> str:
        d = self.interfaces.get("digital_gpio")
        if not d:
            return ""
        return f" Each digital GPIO connector has {_fmt(d.get('connector', []))} pins; the digital GPIO pins are {_fmt(d.get('pins', []))}."

    def render_device(self, key: str) -> str:
        dev = self.devices[key]
        name = pretty(key)
        ik = self.interface_key(key, dev)
        lines = [f"# {name}: connection record"]
        lines.append(f"- Interface: {dev.get('interface', 'not stated')}.")
        for f_, label in (("signal", "Signal pin on the module"), ("connect_to", "Connects to")):
            if f_ in dev:
                lines.append(f"- {label}: {dev[f_]}.")
        if "pins" in dev:
            lines.append("- Module pins: " + "; ".join(f"{p} goes to {t}" for p, t in dev["pins"].items()) + ".")
        if "warning" in dev:
            lines.append(f"- Warning: {dev['warning']}")

        # wiring steps, generated from this device's own fields
        steps: list[str] = []
        digital = ik == "digital_gpio" or "digital" in _norm(str(dev.get("interface", "")))
        hint = self._digital_hint() if digital else ""
        if "pins" in dev:
            for p, t in dev["pins"].items():
                if t.lower().startswith("verify"):
                    steps.append(f"Check the module's own voltage requirement for {p} (the gateway record says: \"{t}\"); the dataset does not give a value.")
                elif "digital gpio" in t.lower():
                    steps.append(f"Connect the module's {p} pin to the Signal pin of an available digital GPIO connector.")
                else:
                    steps.append(f"Connect the module's {p} pin to {t}.")
        elif "signal" in dev:
            steps.append(f"Connect the module's {dev['signal']} signal pin to "
                         + ("the Signal pin of an available digital GPIO connector." if digital else f"{dev.get('connect_to', 'the gateway')}."))
            steps.append("Power and ground: the gateway connector provides "
                         + (_fmt(self.interfaces[ik].get("connector", [])) if ik and "connector" in self.interfaces[ik] else "its own power pins")
                         + ". The dataset does not list this module's own power pin names or voltage, so match the labels printed on the module and check its datasheet.")
        elif "connect_to" in dev:
            steps.append(f"Plug it into the {dev['connect_to']}.")
        if "warning" in dev:
            steps.append(f"Before powering: {dev['warning']}")
        if steps:
            lines += ["", f"# How to wire {name}", *(f"{i}. {s}" for i, s in enumerate(steps, 1))]
            if hint:
                lines.append(f"Digital GPIO on this gateway: {hint.strip()}")

        # the gateway side of the connection (relationship to the interface record)
        if ik:
            lines += ["", f"# Gateway side for {name}", *self._iface_lines(ik)]

        # components: only what the records name (the module itself and the gateway connector it uses)
        target = dev.get("connect_to") or (_fmt(self.interfaces[ik].get("connector", "")) if ik and "connector" in self.interfaces[ik] else "")
        lines += ["", f"# Components needed to connect {name}",
                  f"- The {name} module.", f"- The {self.name}" + (f", using its {target}." if target else "."),
                  "The dataset does not list cables, jumper wires, power supplies, breadboards or any other part: do not add any."]

        group = self._group(key, dev, ik)
        rules = self.rules_for(group)
        lines += ["", f"# Safety checks before powering {name}", *(f"- {r}" for r in rules)]

        concept = self.concept_for(key)
        if concept:
            lines += ["", f"# About the {name}", concept]
        lines += ["", f"# What happens if {name} is wired to the wrong pins",
                  "The dataset does not describe what happens. Do not predict outcomes. The only documented risk is that 5V signals are not "
                  "assumed safe for ESP32 GPIOs (use level shifting when required). Tell the student to re-check the connection record above "
                  "and the labels printed on the gateway."]
        lines += ["", f"# After wiring {name}: what the dataset does and does not cover",
                  "The gateway dataset documents only the physical connection and safety rules above. It does NOT include "
                  f"software or IDE setup, example code or APIs, the expected readings or output, or module-specific troubleshooting for the {name}. "
                  "Say so honestly: do not invent code or steps; suggest checking the module's datasheet or asking the teacher."]
        lines += ["", f"# Troubleshooting {name}: documented checks only",
                  "The dataset has no fault-specific diagnosis. The documented checks are: wiring uses the labels printed on the gateway; "
                  "each module pin goes where the connection record says; module voltage, signal level, current draw and pinout are compatible with the gateway"
                  + ("; a GPIO is not used for two functions at once (GPIO32 is digital OR analog, never both). " if digital else ". ")
                  + "If those are right and it still does not work, the dataset cannot say why: check the module's datasheet or ask the teacher."]
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
    return Gateway(json.loads(Path(path).read_text(encoding="utf-8")))


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
