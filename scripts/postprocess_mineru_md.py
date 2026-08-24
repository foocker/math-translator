r"""Apply narrowly scoped cleanup rules to MinerU Markdown pages.

Only these transformations are performed:
1. Remove every line starting with ``![]`` and, when present, its following
   blank line.
2. Remove any line starting with ``##`` when followed by a blank line.
3. Replace ``$^{†}$`` with a line break.
4. Replace ``$^{\dagger}$`` with a line break.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_DIR / "dictionaries" / "pdf_mineru_md"
DEFAULT_OUTPUT = PROJECT_DIR / "dictionaries" / "pdf_mineru_md_clean"
DAGGER_SEPARATOR = re.compile(r"\$\^\{(?:†|\\dagger)\}\$")
IMAGE_ONLY = re.compile(r"^\s*!\[\]")
HASH_HEADING = re.compile(r"^\s*##")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean numbered MinerU Markdown files")
    parser.add_argument("input_dir", type=Path, nargs="?", default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--in-place", action="store_true", help="overwrite source Markdown files")
    parser.add_argument("--from-page", type=int, default=1)
    parser.add_argument("--to-page", type=int, default=None)
    return parser.parse_args()


def clean_text(text: str) -> tuple[str, bool, int]:
    had_final_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    removed_header = False
    without_image_gaps: list[str] = []
    index = 0
    while index < len(lines):
        if IMAGE_ONLY.match(lines[index]):
            removed_header = True
            index += 1
            if index < len(lines) and not lines[index].strip():
                index += 1
            continue
        removable = HASH_HEADING.match(lines[index])
        if removable and index + 1 < len(lines) and not lines[index + 1].strip():
            removed_header = True
            index += 2
            continue
        without_image_gaps.append(lines[index])
        index += 1
    lines = without_image_gaps

    cleaned: list[str] = []
    split_count = 0
    for line in lines:
        parts = DAGGER_SEPARATOR.split(line)
        split_count += len(parts) - 1
        if len(parts) == 1:
            cleaned.append(line)
            continue
        cleaned.extend(part.strip() for part in parts if part.strip())

    result = "\n".join(cleaned)
    if had_final_newline and result:
        result += "\n"
    return result, removed_header, split_count


def numbered_markdown(input_dir: Path, start: int, end: int | None) -> list[Path]:
    files = [path for path in input_dir.glob("*.md") if path.stem.isdecimal()]
    files.sort(key=lambda path: int(path.stem))
    return [path for path in files if int(path.stem) >= start and (end is None or int(path.stem) <= end)]


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {input_dir}")
    if args.from_page < 1 or (args.to_page is not None and args.to_page < args.from_page):
        raise SystemExit("Invalid page range")
    output_dir = input_dir if args.in_place else args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    files = numbered_markdown(input_dir, args.from_page, args.to_page)
    if not files:
        raise SystemExit("No numbered Markdown files found in requested range")

    changed = 0
    headers_removed = 0
    separators_replaced = 0
    for position, source in enumerate(files, 1):
        original = source.read_text(encoding="utf-8-sig")
        cleaned, removed_header, split_count = clean_text(original)
        destination = output_dir / source.name
        destination.write_text(cleaned, encoding="utf-8", newline="\n")
        changed += cleaned != original
        headers_removed += int(removed_header)
        separators_replaced += split_count
        print(f"[{position}/{len(files)}] {source.name}: header={int(removed_header)}, splits={split_count}")

    print(
        f"Done: files={len(files)}, changed={changed}, headers_removed={headers_removed}, "
        f"separators_replaced={separators_replaced}, output={output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
