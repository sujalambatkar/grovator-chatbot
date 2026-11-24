"""`python -m ingest.vision <image-or-folder> [--application SLUG] [--sensor ID] [--kit ID]`

Describes diagrams and photos with a Groq vision model and writes `knowledge/media/<id>.yaml` + copies the image there.
Everything it writes is `verified: false, origin: vision`: a person must confirm it. Any text that looks pin-related
(GPIO, VCC, GND, SDA, SCL, pin names...) is also appended to `knowledge/GAPS.md` for review, and the bot does not use
unverified pins for wiring answers (wiring tables are built only from application/sensor YAML).
"""
import argparse
import base64
import re
import shutil
import sys
from pathlib import Path

import yaml
from groq import Groq

from app.config import get_settings
from knowledge.loader import KNOWLEDGE_DIR

PROMPT = ("Describe this image for a school IoT lesson. Reply with two lines. Line 1: a caption of at most 12 words. "
          "Line 2: what it shows, listing every label, pin name and connection you can read. Do not guess unreadable text; write 'unreadable'.")
PINLIKE = re.compile(r"\b(?:GPIO\s?\d+|VCC|VIN|GND|SDA|SCL|RX|TX|TXD|RXD|3\.3V|5V|12V|pin)\b", re.I)
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def describe(client: Groq, model: str, path: Path) -> tuple[str, str]:
    data = base64.b64encode(path.read_bytes()).decode()
    res = client.chat.completions.create(
        model=model, max_tokens=500, temperature=0,
        messages=[{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                               {"type": "image_url", "image_url": {"url": f"data:{MIME[path.suffix.lower()]};base64,{data}"}}]}])
    lines = [x.strip() for x in (res.choices[0].message.content or "").strip().splitlines() if x.strip()]
    return (lines[0] if lines else path.stem), " ".join(lines[1:]) or "(no description)"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--application", action="append", default=[])
    ap.add_argument("--sensor", action="append", default=[])
    ap.add_argument("--kit", action="append", default=[])
    ap.add_argument("--model", default="meta-llama/llama-4-scout-17b-16e-instruct")
    a = ap.parse_args(argv)
    s = get_settings()
    if not s.groq_api_key:
        print("GROQ_API_KEY is not set (backend/.env)")
        return 2
    client = Groq(api_key=s.groq_api_key)
    files = [a.path] if a.path.is_file() else sorted(p for p in a.path.iterdir() if p.suffix.lower() in MIME)
    media_dir = KNOWLEDGE_DIR / "media"
    for f in files:
        mid = re.sub(r"[^a-z0-9_]+", "_", f.stem.lower()).strip("_")
        target = media_dir / f"{mid}.yaml"
        if target.exists():
            print(f"skip {f.name}: media/{target.name} already exists (delete it to redo)")
            continue
        caption, shows = describe(client, a.model, f)
        shutil.copy(f, media_dir / f"{mid}{f.suffix.lower()}")
        related = {k: v for k, v in (("application", a.application), ("sensor", a.sensor), ("kit", a.kit)) if v}
        target.write_text(yaml.safe_dump({"id": mid, "file": f"{mid}{f.suffix.lower()}", "caption": caption, "shows": shows, "related": related,
                                          "verified": False, "origin": "vision", "sources": [{"file": f.name, "where": f"described by {a.model}"}]},
                                         sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
        print(f"wrote media/{target.name}: {caption}")
        if PINLIKE.search(shows):
            with open(KNOWLEDGE_DIR / "GAPS.md", "a", encoding="utf-8") as g:
                g.write(f"\n## Vision output to confirm: media/{mid}\n\nThe vision model read pin-like labels from `{f.name}`. Check them against the real image before "
                        f"using them anywhere:\n\n> {shows}\n")
            print("  pin-related text found: added to GAPS.md for confirmation")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
