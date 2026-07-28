#!/usr/bin/env python3
"""Extract matched math terminology from Markdown/LaTeX sources without calling a model."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from math_translate import (
    DEFAULT_GLOSSARY,
    DEFAULT_TERM_TABLE,
    SUPPORTED_SUFFIXES,
    Glossary,
    TranslationError,
    protect_markdown,
)


def iter_source_files(paths: Sequence[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved.is_dir():
            for child in sorted(resolved.rglob("*")):
                if child.is_file() and child.suffix.casefold() in SUPPORTED_SUFFIXES:
                    child = child.resolve()
                    if child not in seen:
                        seen.add(child)
                        yield child
        elif resolved.is_file() and resolved.suffix.casefold() in SUPPORTED_SUFFIXES:
            if resolved not in seen:
                seen.add(resolved)
                yield resolved


def configured_term_tables(args: argparse.Namespace) -> list[Path]:
    if args.no_term_table:
        return []
    values = args.term_table or []
    if not values and DEFAULT_TERM_TABLE.exists():
        values = [str(DEFAULT_TERM_TABLE)]
    return [Path(value).resolve() for value in values]


def collect_terms(args: argparse.Namespace) -> dict[str, Any]:
    files = list(iter_source_files([Path(value) for value in args.inputs]))
    if not files:
        raise TranslationError("没有找到可扫描的 .md/.markdown/.tex/.ltx 文件")

    glossary = Glossary(
        Path(args.glossary).resolve(),
        args.domain,
        configured_term_tables(args),
        args.term_table_single_word,
    )

    occurrences: Counter[tuple[str, str]] = Counter()
    matched_forms: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    domains: dict[tuple[str, str], set[str]] = defaultdict(set)
    notes: dict[tuple[str, str], set[str]] = defaultdict(set)
    files_by_term: dict[tuple[str, str], set[str]] = defaultdict(set)
    protected_segments = 0

    for file_path in files:
        protected = protect_markdown(file_path.read_text(encoding="utf-8"))
        protected_segments += len(protected.tokens)
        for match in glossary.find(protected.text):
            key = (match.term.source, match.term.target)
            occurrences[key] += 1
            matched_forms[key][match.matched_text] += 1
            domains[key].update(match.term.domains)
            if match.term.note:
                notes[key].add(match.term.note)
            files_by_term[key].add(str(file_path))

    ranked = sorted(
        occurrences,
        key=lambda key: (
            -occurrences[key],
            -(len(key[0])),
            key[0].casefold(),
            key[1],
        ),
    )
    if args.min_occurrences > 1:
        ranked = [key for key in ranked if occurrences[key] >= args.min_occurrences]
    omitted = max(0, len(ranked) - args.max_terms)
    ranked = ranked[: args.max_terms]

    terms = []
    for source, target in ranked:
        key = (source, target)
        terms.append(
            {
                "source": source,
                "target": target,
                "occurrences": occurrences[key],
                "matched_forms": [
                    {"form": form, "count": count}
                    for form, count in matched_forms[key].most_common(8)
                ],
                "domains": sorted(domains[key]),
                "notes": sorted(notes[key]),
                "files": sorted(files_by_term[key]),
            }
        )

    return {
        "inputs": [str(path) for path in files],
        "domain": args.domain,
        "document_kind": args.document_kind,
        "active_dictionary_terms": len(glossary.terms),
        "protected_segments": protected_segments,
        "matched_unique_terms": len(occurrences),
        "reported_terms": len(terms),
        "omitted_terms": omitted,
        "terms": terms,
    }


def write_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Matched Mathematics Terminology",
        "",
        f"- Domain: {report.get('domain') or 'unspecified'}",
        f"- Document kind: {report.get('document_kind') or 'unspecified'}",
        f"- Files scanned: {len(report['inputs'])}",
        f"- Protected segments: {report['protected_segments']}",
        f"- Unique matched terms: {report['matched_unique_terms']}",
        f"- Reported terms: {report['reported_terms']}",
        f"- Omitted terms: {report['omitted_terms']}",
        "",
        "| English | Chinese | Count | Matched Forms | Notes |",
        "|---|---|---:|---|---|",
    ]
    for term in report["terms"]:
        forms = ", ".join(f"{item['form']} ({item['count']})" for item in term["matched_forms"])
        notes = "; ".join(term["notes"])
        lines.append(
            f"| {term['source']} | {term['target']} | {term['occurrences']} | {forms} | {notes} |"
        )
    lines.append("")
    return "\n".join(lines)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search the embedded math dictionary against source files and output only matched terms."
    )
    parser.add_argument("inputs", nargs="+", help="files or directories to scan")
    parser.add_argument("--domain", help="math domain, e.g. topology or algebraic_geometry")
    parser.add_argument(
        "--document-kind",
        choices=["paper", "book", "chapter", "lecture_notes", "notes"],
        default="paper",
        help="document type metadata; accepted for consistency with math_translate.py",
    )
    parser.add_argument("--glossary", default=str(DEFAULT_GLOSSARY), help="approved glossary JSON")
    parser.add_argument("--term-table", action="append", help="supplemental TSV dictionary; repeatable")
    parser.add_argument("--no-term-table", action="store_true", help="disable supplemental TSV dictionary")
    parser.add_argument("--term-table-single-word", action="store_true", help="allow selected single-word TSV terms")
    parser.add_argument("--min-occurrences", type=int, default=1, help="minimum occurrences for reported terms")
    parser.add_argument("--max-terms", type=int, default=300, help="maximum terms to report")
    parser.add_argument("--format", choices=["json", "md"], default="md", help="output format")
    parser.add_argument("-o", "--output", help="write report to file instead of stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        report = collect_terms(args)
    except TranslationError as exc:
        parser.exit(2, f"error: {exc}\n")

    if args.format == "json":
        output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    else:
        output = write_markdown(report)
    if args.output:
        Path(args.output).resolve().write_text(output, encoding="utf-8")
    else:
        print(output, end="" if output.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
