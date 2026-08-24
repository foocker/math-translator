#!/usr/bin/env python3
"""Convert the legacy mathematics dictionaries to JSON key-value files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
CJK_RE = re.compile(fr"[{CJK}]")
SINGLE_VARIABLE_RE = re.compile(r"^[A-Za-z]$")


def normalize_source(source: str) -> str:
    source = re.sub(r"\s+", " ", source.strip())
    return re.sub(r"\s+[A-Z]$", "", source).strip()


def add_mapping(mapping: dict[str, str], source: str, target: str) -> bool:
    source = normalize_source(source)
    target = re.sub(r"\s+", "", target.strip())
    if not source or not target or CJK_RE.search(source):
        return False
    # Keep the first value on duplicate keys; this is deterministic and avoids
    # silently changing an existing translation when OCR produced a conflict.
    mapping.setdefault(source, target)
    return True


def convert_tsv(path: Path) -> tuple[dict[str, str], dict[str, int]]:
    mapping: dict[str, str] = {}
    skipped = 0
    for index, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines()):
        if index == 0 and line.casefold().startswith("english\t"):
            continue
        if not line.strip() or "\t" not in line:
            skipped += 1
            continue
        source, target = line.split("\t", 1)
        if not add_mapping(mapping, source, target):
            skipped += 1
    return mapping, {"skipped_lines": skipped, "duplicate_keys": 0}


def looks_like_continuation(prefix: str, line: str, has_previous: bool) -> bool:
    if not has_previous:
        return not prefix or not line[:1].isascii() or not line[:1].isalpha()
    if not prefix or not line[:1].isascii() or not line[:1].isalpha():
        return True
    return bool(SINGLE_VARIABLE_RE.fullmatch(prefix))


def convert_merged_markdown(path: Path) -> tuple[dict[str, str], dict[str, int]]:
    mapping: dict[str, str] = {}
    current_key: str | None = None
    skipped = 0
    continuations = 0
    conflicts = 0
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = re.sub(r"\s+", " ", raw_line.strip())
        if not line:
            continue
        match = CJK_RE.search(line)
        if match is None:
            if current_key is not None:
                mapping[current_key] += line
                continuations += 1
            else:
                skipped += 1
            continue
        prefix, target = line[: match.start()].strip(), line[match.start() :].strip()
        if looks_like_continuation(prefix, line, current_key is not None):
            if current_key is not None:
                mapping[current_key] += (" " if mapping[current_key] else "") + line
                continuations += 1
            else:
                skipped += 1
            continue
        key = normalize_source(prefix)
        if not key or not key[:1].isalpha() or not key[:1].isascii():
            skipped += 1
            continue
        existing = mapping.get(key)
        if existing is not None:
            conflicts += int(existing != target)
            current_key = key
            continue
        mapping[key] = re.sub(r"\s+", "", target)
        current_key = key
    return mapping, {
        "skipped_lines": skipped,
        "continuation_lines": continuations,
        "conflicting_entries": conflicts,
    }


def write_json(path: Path, mapping: dict[str, str], source: Path, stats: dict[str, int]) -> None:
    payload = {
        "version": 1,
        "format": "key-value",
        "source": str(source.name),
        "stats": {"entries": len(mapping), **stats},
        "terms": mapping,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--format", choices=["tsv", "merged-md"], required=True)
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"Input file not found: {args.input}")
    if args.format == "tsv":
        mapping, stats = convert_tsv(args.input)
    else:
        mapping, stats = convert_merged_markdown(args.input)
    write_json(args.output, mapping, args.input, stats)
    print(f"Wrote {len(mapping)} entries to {args.output}")
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
