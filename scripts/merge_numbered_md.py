"""Merge naturally numbered Markdown files into one document."""

from __future__ import annotations

import argparse
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_DIR / "dictionaries" / "pdf_mineru_md_clean"
DEFAULT_OUTPUT = DEFAULT_INPUT / "merged.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge 1.md, 2.md, ... in natural-number order")
    parser.add_argument("input_dir", type=Path, nargs="?", default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    input_dir = args.input_dir.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {input_dir}")
    files = sorted(
        (path for path in input_dir.glob("*.md") if path.stem.isdecimal()),
        key=lambda path: int(path.stem),
    )
    if not files:
        raise SystemExit(f"No numbered Markdown files found in {input_dir}")

    parts: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").rstrip()
        if text:
            parts.append(text)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(parts) + "\n", encoding="utf-8", newline="\n")
    print(f"Merged {len(files)} files into {output}")
    print(f"Output size: {output.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
