"""Repair line boundaries between dictionary entries in a merged Markdown file."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_DIR / "dictionaries" / "merged.md"
CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
JOIN_CJK_EN = re.compile(fr"(?<=[{CJK}])(?=[A-Za-z])")
JOIN_CJK_SPACE_EN = re.compile(fr"(?<=[{CJK}])[ \t]+(?=[A-Za-z])")


def has_cjk(text: str) -> bool:
    return re.search(fr"[{CJK}]", text) is not None


def has_latin(text: str) -> bool:
    return re.search(r"[A-Za-z]", text) is not None


def is_heading(text: str) -> bool:
    return text.lstrip().startswith("#")


def merge_wrapped_entries(lines: list[str]) -> tuple[list[str], int]:
    result: list[str] = []
    joined = 0
    index = 0
    while index < len(lines):
        current = lines[index]
        stripped = current.strip()
        if (
            index + 1 < len(lines)
            and stripped
            and not is_heading(stripped)
            and has_latin(stripped)
            and not has_cjk(stripped)
            and lines[index + 1].strip()
            and not is_heading(lines[index + 1].strip())
            and has_cjk(lines[index + 1].strip())
            and not has_latin(lines[index + 1].strip())
        ):
            result.append(stripped + " " + lines[index + 1].strip())
            joined += 1
            index += 2
            continue
        result.append(current)
        index += 1
    return result, joined


def split_stuck_entries(lines: list[str]) -> tuple[list[str], int]:
    result: list[str] = []
    split_count = 0
    for line in lines:
        line = JOIN_CJK_SPACE_EN.sub("\n", line)
        parts = JOIN_CJK_EN.split(line)
        split_count += len(parts) - 1
        result.extend(parts)
    return result, split_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair dictionary entry line boundaries")
    parser.add_argument("input", type=Path, nargs="?", default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--in-place", action="store_true", help="overwrite the input file")
    args = parser.parse_args()

    source = args.input.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Input file not found: {source}")
    if args.in_place and args.output:
        raise SystemExit("Use either --in-place or --output, not both")
    destination = source if args.in_place else (args.output or source.with_name(source.stem + "_entries.md"))
    text = source.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    lines, joined = merge_wrapped_entries(text.split("\n"))
    lines, split_count = split_stuck_entries(lines)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"Joined wrapped entries: {joined}")
    print(f"Split stuck entries: {split_count}")
    print(f"Saved: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
