"""Pydantic models for the file-based knowledge base (applications, sensors, blocks, kits, media).

Adding knowledge = adding a YAML file that matches one of these models. `python -m knowledge.validate` checks them all.
Unknown keys are rejected so typos in a YAML file are caught instead of silently ignored.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ shared
class Source(_Model):
    """Where a fact came from. `file` is a document name, `where` a page/section."""
    file: str
    where: str = ""


class Faq(_Model):
    q: str
    a: str
    triggers: list[str] = []  # extra phrasings a student may use ("it shows 0", "led not on")


# ------------------------------------------------------------------ sensors
class I2C(_Model):
    address: str
    scl: str
    sda: str
    freq: int | None = None


class UART(_Model):
    uart_id: int
    baudrate: int
    tx_gpio: int
    rx_gpio: int


class SensorPin(_Model):
    pin: str                   # the sensor's own pin label ("VIN", "SDA")
    connects_to: str           # what it connects to on the kit ("3.3V", "SDA (GPIO21)")
    note: str = ""


class KitConnection(_Model):
    """How a sensor connects to ONE kit, as a document states it."""
    kit: str
    interface: str = ""            # interface of the sensor ON THIS KIT (the two lists disagree for a few sensors)
    pins: list[SensorPin] = []
    note: str = ""
    warning: str = ""
    example_code: str = ""         # example program file named in this kit's sensor list
    source: Source | None = None


class Sensor(_Model):
    id: str
    name: str
    aliases: list[str] = []
    category: str = ""             # "Input" / "Output" (sensor list)
    working: str = ""              # how it works (sensor list), shown for "what is X / how does X work"
    example_code: str = ""         # name of the example program in the sensor list (the code itself is not in the documents)
    gateway_key: str = ""          # key of the same device in the generic gateway dataset (knowledge_src), if any
    kit_connections: list[KitConnection] = []
    supersedes: list[str] = []  # keys of older entries in the generic gateway dataset that this record replaces
    description: str
    sensor_type: Literal["INPUT", "OUTPUT"]
    modules: list[str] = []
    device_type: str = ""
    interface: Literal["I2C", "UART", "digital", "analog", "USB", "relay", "dedicated", "none"]
    i2c: I2C | None = None
    uart: UART | None = None
    pins: list[SensorPin] = []
    pins_verified: bool = False
    variables: list[str] = []
    blocks: list[str] = []     # block ids, in the order they are stacked
    how_it_measures: list[str] = []
    notes: list[str] = []
    sources: list[Source] = []


# ------------------------------------------------------------------ blocks
class Block(_Model):
    id: str
    title: str
    category: str = "Sensor"
    sub_category: str = ""     # block sheet: the sensor / group inside the category ("Alcohol Sensor"); empty for older code-bearing blocks
    platform: str = ""         # block sheet: which editor lists the block ("ESP32")
    order: int = 0             # block sheet: position of the block in the sheet (palette order); 0 = not from the sheet
    aliases: list[str] = []
    sensor: str | None = None
    connection: str = "Top - bottom"
    description: str
    language: str = "python"
    code: str = ""             # VERBATIM from the source document (normalised indentation only, see notes); empty = the block sheet gives no code
    code_notes: list[str] = []
    variables: list[str] = []
    variant: str = ""          # which version of the sensor code this block belongs to
    verified: bool = True
    sources: list[Source] = []


# ------------------------------------------------------------------ kits
class PortGroup(_Model):
    name: str
    pins: list[str]
    photo: str = ""            # media id
    note: str = ""


class PinRule(_Model):
    """Which pins of a kit serve one purpose (digital GPIO, analog input, I2C, UART, relay...)."""
    name: str
    pins: list[str]
    note: str = ""


class Kit(_Model):
    id: str
    name: str
    aliases: list[str] = []
    description: str
    verified: bool = False
    port_groups: list[PortGroup] = []
    pin_rules: list[PinRule] = []
    wiring_rules: list[str] = []
    documented_facts: list[str] = []   # only what the documents say
    not_documented: list[str] = []
    sources: list[Source] = []


# ------------------------------------------------------------------ media
class Media(_Model):
    id: str
    file: str                  # relative to knowledge/media/
    caption: str
    shows: str
    related: dict[str, list[str]] = {}   # {"application": [...], "sensor": [...], "kit": [...]}
    verified: bool = False
    origin: Literal["document", "photo", "vision"] = "document"
    sources: list[Source] = []


# ------------------------------------------------------------------ application
class Step(_Model):
    n: int
    title: str
    text: str


class Threshold(_Model):
    name: str
    quantity: Literal["SpO2", "BPM"]
    rule: str
    result: str


class Connection(_Model):
    sensor_pin: str
    kit_pin: str
    note: str = ""
    verified: bool = True


class LedPin(_Model):
    name: str
    gpio: int
    meaning: str


class Circuit(_Model):
    kit: str
    sensor: str
    connections: list[Connection]
    leds: list[LedPin] = []
    notes: list[str] = []
    image: str = ""            # media id


class LineExplanation(_Model):
    code: str
    meaning: list[str]


class Program(_Model):
    variant: str
    description: str
    code: str
    explanations: list[LineExplanation] = []
    code_notes: list[str] = []


class IoTBlocks(_Model):
    input: str
    processor: str
    output: str
    notification: str


class Application(_Model):
    id: str
    title: str
    aliases: list[str] = []
    weak_aliases: list[str] = []  # words that only hint at this application ("led", "spo2"); never enough on their own
    starter_questions: list[str] = []  # chips shown in an empty chat while this application is open
    summary: str
    objectives: list[str]
    challenge: str
    iot_blocks: IoTBlocks
    components: list[str]
    build_steps: list[str] = []  # concrete numbered instructions for "how do I create this application" (only what the documents support)
    steps: list[Step] = Field(min_length=1)
    logic: list[str]
    thresholds: list[Threshold]
    circuit: Circuit
    block_stack: list[str]     # block ids in the order the student stacks them (lesson uses `program` below)
    program: Program
    science: list[str]
    faq: list[Faq] = []
    extension_questions: list[str] = []
    resources: list[str] = []
    safety_note: str = ""
    sources: list[Source] = []
