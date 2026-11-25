"""`python -m knowledge.validate [dir]`: check every knowledge file. Exit code 1 if anything is wrong."""
import sys
from pathlib import Path

from knowledge.loader import KNOWLEDGE_DIR, load


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else KNOWLEDGE_DIR
    k = load(root)
    print(f"knowledge dir: {root}")
    print(f"  applications: {len(k.applications)}  sensors: {len(k.sensors)}  blocks: {len(k.blocks)}  "
          f"kits: {len(k.kits)}  media: {len(k.media)}  docs: {len(k.docs)}")
    for w in k.warnings:
        print(f"  warning: {w}")
    if k.problems:
        print(f"\n{len(k.problems)} problem(s):")
        for p in k.problems:
            print(f"  - {p}")
        return 1
    print("OK: all knowledge files are valid")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
