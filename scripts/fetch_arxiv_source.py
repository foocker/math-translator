#!/usr/bin/env python3
"""Fetch and extract arXiv TeX source from an arXiv ID or URL."""

from __future__ import annotations

import argparse
import gzip
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import Sequence


ARXIV_ID_RE = re.compile(r"(?P<id>(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)")


class ArxivFetchError(RuntimeError):
    pass


def parse_arxiv_id(value: str) -> str:
    match = ARXIV_ID_RE.search(value)
    if not match:
        raise ArxivFetchError(f"cannot find arXiv id in: {value}")
    return match.group("id")


def safe_dir_name(arxiv_id: str) -> str:
    return "arXiv_" + arxiv_id.replace("/", "_")


def safe_extract_tar(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if not str(target).startswith(str(destination)):
                raise ArxivFetchError(f"unsafe archive member path: {member.name}")
        tar.extractall(destination)


def extract_source(download_path: Path, source_dir: Path, arxiv_id: str) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    if tarfile.is_tarfile(download_path):
        safe_extract_tar(download_path, source_dir)
        return

    single_tex = source_dir / f"{arxiv_id.replace('/', '_')}.tex"
    try:
        with gzip.open(download_path, "rb") as gz:
            payload = gz.read()
        single_tex.write_bytes(payload)
        return
    except OSError:
        pass

    single_tex.write_bytes(download_path.read_bytes())


def fetch_arxiv_source(arxiv: str, output_root: Path, overwrite: bool = False) -> Path:
    arxiv_id = parse_arxiv_id(arxiv)
    project_dir = output_root / safe_dir_name(arxiv_id)
    source_dir = project_dir / "paper_source"
    download_path = project_dir / "paper_source.tar.gz"

    if source_dir.exists() and any(source_dir.iterdir()) and not overwrite:
        raise ArxivFetchError(f"source directory already exists: {source_dir}; use --overwrite to replace it")

    if overwrite and source_dir.exists():
        shutil.rmtree(source_dir)
    project_dir.mkdir(parents=True, exist_ok=True)

    url = f"https://arxiv.org/e-print/{arxiv_id}"
    request = urllib.request.Request(url, headers={"User-Agent": "pure-math-translator/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        download_path.write_bytes(response.read())

    extract_source(download_path, source_dir, arxiv_id)
    return project_dir


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and extract arXiv TeX source from an arXiv ID or URL.")
    parser.add_argument("arxiv", help="arXiv ID or URL, e.g. 2401.01234, https://arxiv.org/abs/2401.01234")
    parser.add_argument("-o", "--output-root", default=".", help="directory where arXiv_<ID>/ will be created")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing paper_source directory")
    parser.add_argument("--print-id", action="store_true", help="only parse and print the arXiv ID; do not download")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        arxiv_id = parse_arxiv_id(args.arxiv)
        if args.print_id:
            print(arxiv_id)
            return 0
        project_dir = fetch_arxiv_source(args.arxiv, Path(args.output_root).resolve(), args.overwrite)
    except ArxivFetchError as exc:
        parser.exit(2, f"error: {exc}\n")
    print(project_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
