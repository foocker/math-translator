#!/usr/bin/env python3
"""Orchestrate Codex agents that turn PaddleOCR page Markdown into TeX.

This module never edits ``doc_N.md``. It stages read-only copies for isolated
Codex runs, imports validated agent artifacts, and performs only final TeX
assembly in Python. Boundary detection is local and deterministic; agents only
extract the table-of-contents outline, map chapter content to TeX, and review it.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


TOOL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = TOOL_DIR / "output"
DEFAULT_TEMPLATE_DIR = TOOL_DIR / "templates" / "elegantbook"
PAGE_PATTERN = re.compile(r"doc_(\d+)\.md", re.IGNORECASE)
CLASSIFICATION_EDGE_WINDOW = 32
CONTENTS_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:table\s+of\s+contents|contents|目录)\s*$",
    re.IGNORECASE,
)
BACK_MATTER_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:"
    r"bibliography|references?|index|name\s+index|glossary|"
    r"biographical\s+notes?|about\s+the\s+authors?|chronology|"
    r"参考文献|参考书目|索引|人名索引|术语表|"
    r".*?(?:作者|人物|人名|学者|数学家|科学家).*?(?:生卒|简介|生平|传略|年表).*"
    r")\s*$",
    re.IGNORECASE,
)


class AgentPipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourcePage:
    number: int
    path: Path
    chars: int


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    pages: tuple[SourcePage, ...]
    chapter_number: int | None = None
    chapter_title: str | None = None
    section_titles: tuple[str, ...] = ()


class CodexRunner:
    def __init__(
        self,
        codex_bin: str,
        model: str | None,
        reasoning: str | None,
        timeout: int,
        dangerously_bypass: bool,
    ) -> None:
        resolved = shutil.which(codex_bin)
        if resolved is None and not Path(codex_bin).exists():
            raise AgentPipelineError(f"找不到 Codex CLI：{codex_bin}")
        self.codex_bin = resolved or codex_bin
        self.model = model
        self.reasoning = reasoning
        self.timeout = timeout
        self.dangerously_bypass = dangerously_bypass

    def run(self, workdir: Path, prompt: str) -> None:
        final_message = workdir / "agent-final.txt"
        command = [self.codex_bin]
        if self.dangerously_bypass:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        command.append("exec")
        if self.model:
            command.extend(["--model", self.model])
        if self.reasoning:
            command.extend(["--config", f'model_reasoning_effort="{self.reasoning}"'])
        command.extend(
            [
                "--ephemeral",
                "--skip-git-repo-check",
                "--color",
                "never",
                "-C",
                str(workdir),
                "--output-last-message",
                str(final_message),
                "-",
            ]
        )
        if not self.dangerously_bypass:
            command[command.index("exec") + 1:command.index("exec") + 1] = ["--sandbox", "workspace-write"]
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentPipelineError(f"Codex Agent 超时（{self.timeout} 秒）：{workdir.name}") from exc
        (workdir / "codex.stdout.log").write_text(completed.stdout or "", encoding="utf-8")
        (workdir / "codex.stderr.log").write_text(completed.stderr or "", encoding="utf-8")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-3000:]
            raise AgentPipelineError(f"Codex Agent 失败（退出码 {completed.returncode}）：\n{detail}")


def discover_pages(output_dir: Path, page_count: int | None = None) -> list[SourcePage]:
    if page_count is not None:
        candidates = [output_dir / f"doc_{number}.md" for number in range(page_count)]
        missing = [path.name for path in candidates if not path.is_file()]
        if missing:
            raise AgentPipelineError(f"OCR 页面缺失：{', '.join(missing[:20])}")
    else:
        candidates = []
        for path in output_dir.glob("doc_*.md"):
            if PAGE_PATTERN.fullmatch(path.name):
                candidates.append(path)
    pages = [
        SourcePage(
            int(PAGE_PATTERN.fullmatch(path.name).group(1)),
            path,
            len(path.read_text(encoding="utf-8", errors="replace")),
        )
        for path in candidates
        if PAGE_PATTERN.fullmatch(path.name)
    ]
    pages.sort(key=lambda page: page.number)
    if not pages:
        raise AgentPipelineError(f"在 {output_dir} 中没有找到 doc_N.md")
    return pages


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise AgentPipelineError(f"Agent 未生成文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise AgentPipelineError(f"Agent JSON 无效：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise AgentPipelineError(f"Agent JSON 顶层必须是对象：{path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")


def timestamped_run_dir(work_root: Path, label: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = work_root / "runs" / f"{label}-{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def copy_common_agent_context(run_dir: Path, template_dir: Path) -> None:
    shutil.copy2(template_dir / "AGENTS.md", run_dir / "AGENTS.md")
    shutil.copy2(template_dir / "template.json", run_dir / "template.json")
    shutil.copy2(template_dir / "preamble.tex", run_dir / "preamble.tex")


def copy_page(run_dir: Path, page: SourcePage) -> None:
    target = run_dir / "source" / page.path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(page.path, target)


def image_references(text: str) -> set[str]:
    references = set(re.findall(r"<img\b[^>]*?src=[\"']([^\"']+)[\"']", text, re.IGNORECASE))
    references.update(re.findall(r"!\[[^]]*\]\(([^)]+)\)", text))
    return {reference.strip().replace("\\", "/") for reference in references if reference.strip()}


def copy_referenced_images(run_dir: Path, output_dir: Path, pages: Iterable[SourcePage]) -> list[str]:
    missing: list[str] = []
    for page in pages:
        text = page.path.read_text(encoding="utf-8", errors="replace")
        for reference in image_references(text):
            if reference.startswith(("http://", "https://")):
                continue
            source = (output_dir / reference).resolve()
            try:
                source.relative_to(output_dir.resolve())
            except ValueError:
                missing.append(reference)
                continue
            if not source.is_file():
                missing.append(reference)
                continue
            target = run_dir / "source" / Path(reference.replace("/", os.sep))
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)
    return sorted(set(missing))


def normalized_nonempty_lines(page: SourcePage) -> list[str]:
    return [
        re.sub(r"\s+", " ", line.strip())
        for line in page.path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]


def is_contents_page(page: SourcePage, continuation: bool = False) -> bool:
    lines = normalized_nonempty_lines(page)
    if not lines:
        return continuation
    if any(CONTENTS_HEADING_RE.fullmatch(line) for line in lines[:5]):
        return True
    toc_entries = sum(
        bool(re.search(r"(?:\.{2,}|…{2,}|\s)\s*\d+\s*$", line))
        for line in lines
    )
    return continuation and toc_entries >= 2


def is_back_matter_boundary(page: SourcePage) -> bool:
    lines = normalized_nonempty_lines(page)
    candidates = lines[:8] + lines[-3:]
    return any(BACK_MATTER_HEADING_RE.fullmatch(line) for line in candidates)


def detect_boundaries(pages: Sequence[SourcePage]) -> tuple[int, int, int | None]:
    """Inspect only the document edges and return TOC/body/end boundaries."""
    leading = pages[: min(CLASSIFICATION_EDGE_WINDOW, len(pages))]
    contents_index = next(
        (index for index, page in enumerate(leading) if is_contents_page(page)),
        None,
    )
    if contents_index is None:
        raise AgentPipelineError(
            f"前 {len(leading)} 个 OCR 页面中未找到目录；请检查 doc_N.md 或增大 CLASSIFICATION_EDGE_WINDOW"
        )
    contents_page = pages[contents_index].number
    cursor = contents_index
    while cursor + 1 < len(pages) and is_contents_page(pages[cursor + 1], continuation=True):
        cursor += 1
    body_index = cursor + 1
    while body_index < len(pages) and not normalized_nonempty_lines(pages[body_index]):
        body_index += 1
    if body_index >= len(pages):
        raise AgentPipelineError("目录之后没有可转换的正文页面")
    body_start = pages[body_index].number

    trailing = pages[body_index:]
    end_candidates = [page.number for page in trailing if is_back_matter_boundary(page)]
    end_page = min(end_candidates) if end_candidates else None
    return contents_page, body_start, end_page


def infer_document_metadata(pages: Sequence[SourcePage], contents_page: int) -> tuple[str, str]:
    title = "OCR Document"
    author = ""
    for page in pages:
        if page.number >= contents_page:
            break
        lines = normalized_nonempty_lines(page)
        for index, line in enumerate(lines):
            heading = re.match(r"^#\s+(.+)$", line)
            if not heading:
                continue
            title = heading.group(1).strip()
            if index + 1 < len(lines):
                candidate = re.sub(r"^[【\[].*?[】\]]\s*", "", lines[index + 1]).strip()
                if candidate and not candidate.startswith("#"):
                    author = candidate
            return title, author
    return title, author


def body_page_index(manifest: dict[str, Any], pages: Sequence[SourcePage]) -> list[dict[str, Any]]:
    """Build a compact structural index without interpreting chapter semantics."""
    result: list[dict[str, Any]] = []
    for page in body_pages_from_manifest(manifest, pages):
        lines = normalized_nonempty_lines(page)
        headings = [line for line in lines if re.match(r"^#{1,6}\s+", line)]
        result.append(
            {
                "page": page.number,
                "file": page.path.name,
                "headings": headings[:8],
                "opening": lines[:3],
            }
        )
    return result


def validate_manifest(manifest: dict[str, Any], pages: Sequence[SourcePage]) -> None:
    page_numbers = {page.number for page in pages}
    contents_page = manifest.get("contents_page")
    body_start = manifest.get("body_start_page")
    end_page = manifest.get("end_page_excluded")
    if not isinstance(contents_page, int) or contents_page not in page_numbers:
        raise AgentPipelineError("page_manifest.json 缺少有效 contents_page")
    if not isinstance(body_start, int) or body_start not in page_numbers:
        raise AgentPipelineError("page_manifest.json 缺少有效 body_start_page")
    if contents_page >= body_start:
        raise AgentPipelineError("正文起点必须位于完整目录之后")
    if end_page is not None and (not isinstance(end_page, int) or end_page not in page_numbers):
        raise AgentPipelineError("end_page_excluded 必须是源页面编号或 null")
    if isinstance(end_page, int) and end_page <= body_start:
        raise AgentPipelineError("正文结束边界必须位于正文起点之后")


def run_classification(pages: Sequence[SourcePage], work_root: Path, overwrite: bool = False) -> Path:
    """Create the inexpensive edge-only page manifest without an Agent call."""
    destination = work_root / "page_manifest.json"
    if destination.is_file() and not overwrite:
        load_manifest(destination, pages)
        print(f"沿用页面边界清单：{destination}", file=sys.stderr)
        return destination
    contents_page, body_start, end_page = detect_boundaries(pages)
    title, author = infer_document_metadata(pages, contents_page)
    manifest = {
        "version": 3,
        "title": title,
        "author": author,
        "contents_page": contents_page,
        "body_start_page": body_start,
        "end_page_excluded": end_page,
    }
    validate_manifest(manifest, pages)
    previous = read_json(destination) if destination.is_file() else None
    if previous != manifest:
        write_json(destination, manifest)
        print(f"更新页面边界清单：{destination}", file=sys.stderr)
    else:
        print(f"页面边界清单未变化：{destination}", file=sys.stderr)
    return destination


def load_manifest(path: Path, pages: Sequence[SourcePage]) -> dict[str, Any]:
    manifest = read_json(path)
    validate_manifest(manifest, pages)
    return manifest


def body_pages_from_manifest(manifest: dict[str, Any], pages: Sequence[SourcePage]) -> list[SourcePage]:
    start = manifest["body_start_page"]
    end = manifest.get("end_page_excluded")
    return [page for page in pages if page.number >= start and (end is None or page.number < end)]


def outline_prompt() -> str:
    return """Work as the document-outline agent described in AGENTS.md.

Read the staged table-of-contents source/doc_N.md files, page_manifest.json,
and body_page_index.json. Reconstruct the book's authoritative chapter and
section structure before any TeX conversion begins. The body index is
mechanically extracted evidence for locating starts; repeated running headers
are not chapter boundaries. Do not translate anything.

Write chapter_manifest.json with this exact shape:
{
  "version": 2,
  "source": "table-of-contents-and-body-index",
  "chapters": [
    {
      "number": 1,
      "title": "complete original-language chapter title",
      "printed_start_page": 1,
      "ocr_start_page": 6,
      "ocr_end_page": 25,
      "sections": [
        {
          "title": "complete original-language section title",
          "printed_start_page": 1,
          "ocr_start_page": 6
        }
      ]
    }
  ],
  "body_end": {
    "heading": "first listed non-body item after the final chapter, or empty",
    "printed_start_page": 0,
    "ocr_start_page": 0
  }
}

Required behavior:
- Extract every top-level chapter listed in the table of contents, regardless
  of language, numbering style, or chapter count. The JSON number is only a
  consecutive internal sequence beginning with 1.
- Preserve chapter and section titles as printed, removing only dot leaders
  and trailing page numbers that are not part of the title.
- Map every chapter directly to inclusive OCR doc_N page bounds. Use printed
  page numbers, page order, and matching evidence in body_page_index.json.
- Chapter ranges must be ordered, non-overlapping, and contiguous. The final
  chapter ends immediately before page_manifest.end_page_excluded when that
  boundary is present.
- Use null when a printed page number, section OCR start, or body-end item is
  absent or cannot be established. Do not invent evidence.
- Do not edit source files. Finish only after chapter_manifest.json is valid JSON.
"""


def validate_chapter_manifest(
    value: dict[str, Any], manifest: dict[str, Any], pages: Sequence[SourcePage]
) -> None:
    chapters = value.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise AgentPipelineError("chapter_manifest.json 必须包含至少一章")
    if any(not isinstance(chapter, dict) for chapter in chapters):
        raise AgentPipelineError("chapter_manifest.json 包含无效章节项")
    expected_numbers = list(range(1, len(chapters) + 1))
    if [chapter["number"] for chapter in chapters] != expected_numbers:
        raise AgentPipelineError(f"chapter_manifest.json 的章节编号必须连续：{expected_numbers}")
    page_numbers = {page.number for page in pages}
    previous_end: int | None = None
    for chapter in chapters:
        if not isinstance(chapter.get("title"), str) or not chapter["title"].strip():
            raise AgentPipelineError("chapter_manifest.json 包含空章名")
        if chapter.get("printed_start_page") is not None and not isinstance(chapter.get("printed_start_page"), int):
            raise AgentPipelineError("chapter_manifest.json 章节印刷页码无效")
        start = chapter.get("ocr_start_page")
        end = chapter.get("ocr_end_page")
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            raise AgentPipelineError("chapter_manifest.json 缺少有效 OCR 起止页")
        if start not in page_numbers or end not in page_numbers:
            raise AgentPipelineError("chapter_manifest.json 章节范围引用了不存在的 OCR 页面")
        if any(number not in page_numbers for number in range(start, end + 1)):
            raise AgentPipelineError("chapter_manifest.json 章节范围包含缺失的 OCR 页面")
        if start < manifest["body_start_page"]:
            raise AgentPipelineError("chapter_manifest.json 章节起点位于正文边界之前")
        if previous_end is not None and start != previous_end + 1:
            raise AgentPipelineError("chapter_manifest.json 章节范围必须连续且不重叠")
        previous_end = end
        sections = chapter.get("sections")
        if not isinstance(sections, list):
            raise AgentPipelineError("chapter_manifest.json 缺少章节小节清单")
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("title"), str):
                raise AgentPipelineError("chapter_manifest.json 包含无效小节")
            if section.get("printed_start_page") is not None and not isinstance(section.get("printed_start_page"), int):
                raise AgentPipelineError("chapter_manifest.json 小节印刷页码无效")
            section_start = section.get("ocr_start_page")
            if section_start is not None and (
                not isinstance(section_start, int) or not start <= section_start <= end
            ):
                raise AgentPipelineError("chapter_manifest.json 小节 OCR 起点不属于所在章节")
    expected_end = manifest.get("end_page_excluded")
    if isinstance(expected_end, int) and previous_end != expected_end - 1:
        raise AgentPipelineError("chapter_manifest.json 最后一章未覆盖到正文结束边界")
    body_end = value.get("body_end")
    if not isinstance(body_end, dict):
        raise AgentPipelineError("chapter_manifest.json 缺少正文结束标记")
    if not isinstance(body_end.get("heading", ""), str):
        raise AgentPipelineError("chapter_manifest.json 正文结束标题无效")
    if body_end.get("printed_start_page") is not None and not isinstance(body_end.get("printed_start_page"), int):
        raise AgentPipelineError("chapter_manifest.json 正文结束页码无效")
    body_end_start = body_end.get("ocr_start_page")
    if body_end_start is not None and not isinstance(body_end_start, int):
        raise AgentPipelineError("chapter_manifest.json 正文结束 OCR 页码无效")
    if isinstance(expected_end, int) and body_end_start not in {None, expected_end}:
        raise AgentPipelineError("chapter_manifest.json 正文结束 OCR 页与页面边界不一致")


def run_outline_agent(
    runner: CodexRunner,
    pages: Sequence[SourcePage],
    manifest: dict[str, Any],
    work_root: Path,
    template_dir: Path,
    overwrite: bool,
) -> dict[str, Any]:
    outline_path = work_root / "chapter_manifest.json"
    toc_numbers = set(
        range(manifest["contents_page"], manifest["body_start_page"])
    )
    toc_pages = [page for page in pages if page.number in toc_numbers]
    if not toc_pages:
        raise AgentPipelineError("页面边界清单中没有目录页")
    if outline_path.is_file() and not overwrite:
        outline = read_json(outline_path)
        try:
            validate_chapter_manifest(outline, manifest, pages)
        except AgentPipelineError as exc:
            print(f"已有目录章节清单已过期，重新生成：{exc}", file=sys.stderr)
        else:
            print(f"沿用目录章节清单：{outline_path}", file=sys.stderr)
            return outline
    run_dir = timestamped_run_dir(work_root, "outline")
    copy_common_agent_context(run_dir, template_dir)
    for page in toc_pages:
        copy_page(run_dir, page)
    write_json(run_dir / "page_manifest.json", manifest)
    write_json(run_dir / "body_page_index.json", {"version": 1, "pages": body_page_index(manifest, pages)})
    print(f"启动目录结构 Agent：{run_dir}", file=sys.stderr)
    runner.run(run_dir, outline_prompt())
    outline = read_json(run_dir / "chapter_manifest.json")
    validate_chapter_manifest(outline, manifest, pages)
    write_json(outline_path, outline)
    print(f"目录章节清单：{outline_path}", file=sys.stderr)
    return outline


def plan_chapter_chunks(
    outline: dict[str, Any], pages: Sequence[SourcePage], max_chars: int
) -> list[Chunk]:
    """Split OCR pages inside the TOC-defined chapter ranges."""
    # The manifest was validated against the current OCR page set when loaded.
    chapters = outline["chapters"]
    expected_numbers = list(range(1, len(chapters) + 1))
    if [chapter["number"] for chapter in chapters] != expected_numbers:
        raise AgentPipelineError(
            f"目录章节不完整：期望 {expected_numbers}，实际为 {[chapter['number'] for chapter in chapters]}"
        )
    page_map = {page.number: page for page in pages}
    chunks: list[Chunk] = []
    for chapter in chapters:
        chapter_pages = [
            page_map[number]
            for number in range(chapter["ocr_start_page"], chapter["ocr_end_page"] + 1)
            if number in page_map
        ]
        if not chapter_pages:
            raise AgentPipelineError(f"第 {chapter['number']} 章没有可用 OCR 页面")
        total_chars = sum(page.chars for page in chapter_pages)
        part_count = max(1, (total_chars + max_chars - 1) // max_chars)
        if part_count == 1:
            parts = [Chunk("part", tuple(chapter_pages))]
        else:
            boundaries = [0]
            cumulative = 0
            next_target = total_chars / part_count
            for index, page in enumerate(chapter_pages[:-1], start=1):
                cumulative += page.chars
                if cumulative >= next_target and len(boundaries) < part_count:
                    boundaries.append(index)
                    next_target = total_chars * len(boundaries) / part_count
            boundaries.append(len(chapter_pages))
            parts = [
                Chunk("part", tuple(chapter_pages[boundaries[i] : boundaries[i + 1]]))
                for i in range(len(boundaries) - 1)
            ]
        for part_number, part in enumerate(parts):
            chunks.append(
                Chunk(
                    f"chapter_{chapter['number']:02d}_part_{part_number:02d}",
                    part.pages,
                    chapter["number"],
                    chapter["title"].strip(),
                    (),
                )
            )
    sections_by_chapter = {
        chapter["number"]: tuple(
            (section["title"].strip(), section.get("ocr_start_page"))
            for section in chapter["sections"]
        )
        for chapter in outline["chapters"]
    }
    return [
        Chunk(
            chunk.chunk_id,
            chunk.pages,
            chunk.chapter_number,
            chunk.chapter_title,
            tuple(
                title
                for title, start_page in sections_by_chapter.get(chunk.chapter_number, ())
                if isinstance(start_page, int)
                and chunk.pages[0].number <= start_page <= chunk.pages[-1].number
            ),
        )
        for chunk in chunks
    ]


def chunk_prompt(
    chunk: Chunk,
    previous_page: int | None,
    next_page: int | None,
) -> str:
    page_files = [page.path.name for page in chunk.pages]
    return f"""Work as the OCR-to-TeX conversion agent described in AGENTS.md.

Read AGENTS.md, template.json, preamble.tex, and chunk_task.json. Convert only
these owned OCR pages, in order:
{json.dumps(page_files, ensure_ascii=False)}

Neighbor page numbers are previous={previous_page!r}, next={next_page!r}. They
are context-only when present under source/; do not duplicate their content.
This chunk belongs to chapter {chunk.chapter_number}: {chunk.chapter_title}.
The authoritative section titles whose starts fall inside this
chunk, in order, are:
{json.dumps(list(chunk.section_titles), ensure_ascii=False)}

Required behavior:
- Do not edit anything under source/.
- Preserve the source language exactly. Chinese remains Chinese, English remains
  English, and mixed-language passages remain mixed-language. Do not translate
  prose, headings, captions, theorem names, proofs, or exercises.
- Produce TeX body fragments in the source language. Translation is an optional
  later stage and is not part of OCR-to-TeX conversion.
- Do not emit a chapter command. The root assembler owns all chapter
  titles and obtains them exclusively from the OCR table of contents. Emit only
  structure inside this chapter, and do not promote repeated running headers or
  exercise statements to section/chapter headings.
- Emit section commands only for authoritative titles above when their actual
  start occurs in an owned page. A part that begins mid-section must continue
  prose without inventing or repeating the section heading.
- Interpret Markdown semantically: preserve chapter/section hierarchy,
  theorem-like statements, proofs, exercises, lists, tables, inline/display
  mathematics, and page-boundary continuations.
- Map Markdown structure to native TeX environments instead of leaving source
  markup in the output: headings to sectioning commands, ordered/unordered
  lists to enumerate/itemize, HTML tables to table/tabular (or array when the
  table is mathematical), theorem-like statements to elegantbook theorem
  environments, and images to figure floats with relative paths.
- Do not wrap ordinary prose paragraphs in `\\text{{...}}`; that causes long
  unbreakable lines and overfull boxes. Use plain TeX prose with short inline
  math only. Break long displayed mathematics with aligned/gathered when
  needed.
- HTML tables must be fully converted. Never copy `<table>`, `<tr>`, or `<td>`
  into chunk.tex. Constrain wide tables to `\\linewidth` with flexible columns
  or `\\resizebox{{\\linewidth}}{{!}}{{...}}`.
- Keep each semantic block structurally complete. Do not flatten a theorem,
  list, table, or display formula into a plain paragraph merely to make the
  conversion shorter.
- Preserve OCR text as source evidence. Do not perform spelling, symbol,
  punctuation, O/0, subscript, superscript, or other content correction.
- Convert referenced images to TeX figures. Use minipages only when images are
  genuinely related and intended side by side. Final paths must remain relative
  to the book root, normally imgs/<name>.
- Preserve a nearby source caption or label such as “图 3.1” in `\\caption{{...}}`.
  Remove OCR placeholders such as `Image`; if no caption is available, omit
  the caption instead of inventing one. Every image width must be bounded by
  `\\linewidth` or `\\textwidth`.
- Do not emit documentclass, preamble, begin{{document}}, end{{document}}, title,
  maketitle, tableofcontents, bibliography, or references.

Write chunk.tex containing only the TeX body for the owned pages.
Write chunk.report.json with this exact shape:
{{
  "version": 1,
  "chunk_id": "{chunk.chunk_id}",
  "source_pages": {[page.number for page in chunk.pages]},
  "structure_notes": [],
  "image_notes": [],
  "completeness": {{"passed": true, "overall_coverage": 1.0, "missing_pages": []}}
}}

Finish only after both files exist and chunk.report.json is valid JSON.
"""


def validate_chunk_artifacts(tex_path: Path, report_path: Path, chunk: Chunk) -> dict[str, Any]:
    if not tex_path.is_file() or not tex_path.read_text(encoding="utf-8", errors="replace").strip():
        raise AgentPipelineError(f"Agent 未生成有效 TeX：{tex_path}")
    tex = tex_path.read_text(encoding="utf-8", errors="replace")
    if tex.lstrip().startswith("```"):
        raise AgentPipelineError(f"TeX 分块不能包含 Markdown 代码围栏：{tex_path}")
    forbidden = ("\\documentclass", "\\begin{document}", "\\end{document}", "\\bibliography")
    if any(value in tex for value in forbidden):
        raise AgentPipelineError(f"TeX 分块包含根文档命令：{tex_path}")
    if re.search(r"<\s*/?(?:table|tr|td|th)\b", tex, re.IGNORECASE):
        raise AgentPipelineError(f"TeX 分块仍包含未转换的 HTML 表格：{tex_path}")
    if re.search(r"\\caption\s*\{\s*Image\s*\}", tex, re.IGNORECASE):
        raise AgentPipelineError(f"TeX 分块包含无意义的 Image 图片标题：{tex_path}")
    long_text_wrappers = sum(len(re.findall(r"\\text\s*\{", line)) for line in tex.splitlines())
    prose_wrapper_lines = sum(
        1
        for line in tex.splitlines()
        if len(line) > 700 and len(re.findall(r"\\text\s*\{", line)) >= 3
    )
    if long_text_wrappers >= 40 or prose_wrapper_lines >= 2:
        raise AgentPipelineError(
            f"TeX 分块疑似将整段正文包进 \\text{{...}}（{long_text_wrappers} 处）：{tex_path}"
        )
    if chunk.chapter_number is not None and re.search(r"\\chapter\*?\s*\{", tex):
        raise AgentPipelineError(
            f"章节内分块不得生成 \\chapter；章名由目录清单统一组装：{tex_path}"
        )
    report = read_json(report_path)
    if report.get("chunk_id") != chunk.chunk_id:
        raise AgentPipelineError(f"chunk_id 不匹配：{report_path}")
    expected_pages = [page.number for page in chunk.pages]
    if report.get("source_pages") != expected_pages:
        raise AgentPipelineError(f"source_pages 不匹配：{report_path}")
    for key in ("structure_notes", "image_notes"):
        if not isinstance(report.get(key), list):
            raise AgentPipelineError(f"分块报告缺少数组 {key}：{report_path}")
    return report


_GENERIC_CONTENT_TOKENS = {
    "begin", "end", "table", "tabular", "array", "figure", "caption",
    "includegraphics", "text", "style", "border", "width", "height",
    "align", "center", "left", "right", "word", "wrap", "break", "html",
    "frac", "sqrt", "cdots", "ldots", "dots", "qquad", "quad", "hfill",
}


def _content_tokens(text: str) -> set[str]:
    """Extract stable OCR words for a conservative source/output audit."""
    tokens = set(re.findall(r"[\u3400-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9_]{3,}", text))
    return {token for token in tokens if token.lower() not in _GENERIC_CONTENT_TOKENS}


def content_completeness(tex_path: Path, chunk: Chunk) -> dict[str, Any]:
    """Check that an Agent fragment contains material from every owned page.

    This is intentionally a recall-oriented audit, not an OCR correction pass:
    a page is considered represented when several stable source tokens survive
    in the TeX fragment.  It catches silent Agent truncation while allowing
    formulas, headings, and page-boundary joins to be rewritten.
    """
    tex = tex_path.read_text(encoding="utf-8", errors="replace")
    output_tokens = _content_tokens(tex)
    page_results: list[dict[str, Any]] = []
    weighted_hits = 0
    weighted_tokens = 0
    missing_pages: list[int] = []
    for page in chunk.pages:
        source = page.path.read_text(encoding="utf-8", errors="replace")
        tokens = _content_tokens(source)
        if len(source.strip()) < 160 or not tokens:
            page_results.append({"page": page.number, "tokens": len(tokens), "coverage": 1.0})
            continue
        hits = len(tokens & output_tokens)
        coverage = hits / len(tokens)
        weighted_hits += hits
        weighted_tokens += len(tokens)
        if coverage < 0.20:
            missing_pages.append(page.number)
        page_results.append(
            {"page": page.number, "tokens": len(tokens), "hits": hits, "coverage": round(coverage, 3)}
        )
    overall = weighted_hits / weighted_tokens if weighted_tokens else 1.0
    allowed_missing = max(1, len(chunk.pages) // 10)
    passed = overall >= 0.72 and len(missing_pages) <= allowed_missing
    return {
        "passed": passed,
        "overall_coverage": round(overall, 3),
        "missing_pages": missing_pages,
        "allowed_missing_pages": allowed_missing,
        "pages": page_results,
    }


def stage_chunk_sources(
    run_dir: Path,
    output_dir: Path,
    all_pages: Sequence[SourcePage],
    chunk: Chunk,
) -> tuple[int | None, int | None, list[str]]:
    page_index = {page.number: index for index, page in enumerate(all_pages)}
    first_index = page_index[chunk.pages[0].number]
    last_index = page_index[chunk.pages[-1].number]
    context_pages = list(chunk.pages)
    previous_page = all_pages[first_index - 1] if first_index > 0 else None
    next_page = all_pages[last_index + 1] if last_index + 1 < len(all_pages) else None
    if previous_page:
        context_pages.insert(0, previous_page)
    if next_page:
        context_pages.append(next_page)
    for page in context_pages:
        copy_page(run_dir, page)
    missing = copy_referenced_images(run_dir, output_dir, context_pages)
    return (
        previous_page.number if previous_page else None,
        next_page.number if next_page else None,
        missing,
    )


def write_chunks_index(work_root: Path, chunks: Sequence[Chunk]) -> Path:
    path = work_root / "chunks.json"
    write_json(
        path,
        {
            "version": 1,
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "pages": [page.number for page in chunk.pages],
                    "files": [page.path.name for page in chunk.pages],
                    "characters": sum(page.chars for page in chunk.pages),
                    "chapter_number": chunk.chapter_number,
                    "chapter_title": chunk.chapter_title,
                    "section_titles": list(chunk.section_titles),
                }
                for chunk in chunks
            ],
        },
    )
    return path


def recover_latest_chunk_artifacts(
    work_root: Path,
    chunk: Chunk,
    final_tex: Path,
    final_report: Path,
) -> bool:
    """Import a valid completed Agent run left behind by an interrupted batch."""
    runs_root = work_root / "runs"
    if not runs_root.is_dir():
        return False
    candidates = sorted(
        (path for path in runs_root.glob(f"{chunk.chunk_id}-*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for run_dir in candidates:
        tex_path = run_dir / "chunk.tex"
        report_path = run_dir / "chunk.report.json"
        if not tex_path.is_file() or not report_path.is_file():
            continue
        try:
            report = validate_chunk_artifacts(tex_path, report_path, chunk)
            audit = content_completeness(tex_path, chunk)
        except (AgentPipelineError, OSError, ValueError):
            continue
        if not audit["passed"]:
            continue
        report["completeness"] = audit
        write_json(report_path, report)
        shutil.copy2(tex_path, final_tex)
        shutil.copy2(report_path, final_report)
        print(f"恢复已完成转换：{chunk.chunk_id}（{run_dir.name}）", file=sys.stderr)
        return True
    return False


def run_conversion(
    runner: CodexRunner,
    pages: Sequence[SourcePage],
    manifest: dict[str, Any],
    output_dir: Path,
    work_root: Path,
    template_dir: Path,
    chunk_chars: int,
    overwrite: bool,
    workers: int,
    outline_overwrite: bool = False,
) -> list[Chunk]:
    body_pages = body_pages_from_manifest(manifest, pages)
    outline = run_outline_agent(
        runner,
        pages,
        manifest,
        work_root,
        template_dir,
        outline_overwrite,
    )
    chunks = plan_chapter_chunks(outline, body_pages, chunk_chars)
    write_chunks_index(work_root, chunks)
    chapters_dir = output_dir / "chapters"
    reports_dir = work_root / "reports"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    def convert_one(item: tuple[int, Chunk]) -> str:
        position, chunk = item
        final_tex = chapters_dir / f"{chunk.chunk_id}.tex"
        final_report = reports_dir / f"{chunk.chunk_id}.json"
        if not overwrite and final_tex.is_file() and final_report.is_file():
            try:
                existing_report = validate_chunk_artifacts(final_tex, final_report, chunk)
            except (AgentPipelineError, OSError, ValueError) as exc:
                print(f"已有分块无效，将重新生成 {chunk.chunk_id}：{exc}", file=sys.stderr)
            else:
                audit = content_completeness(final_tex, chunk)
                if audit["passed"]:
                    if existing_report.get("completeness") != audit:
                        existing_report["completeness"] = audit
                    write_json(final_report, existing_report)
                    print(f"跳过已有分块 {position}/{len(chunks)}：{chunk.chunk_id}", file=sys.stderr)
                    return chunk.chunk_id
                if not audit["passed"]:
                    print(
                        f"完整度不足，将重新转换 {chunk.chunk_id}："
                        f"覆盖率 {audit['overall_coverage']:.1%}，缺失页 {audit['missing_pages']}",
                        file=sys.stderr,
                    )
        if not overwrite and recover_latest_chunk_artifacts(
            work_root, chunk, final_tex, final_report
        ):
            return chunk.chunk_id
        run_dir = timestamped_run_dir(work_root, chunk.chunk_id)
        copy_common_agent_context(run_dir, template_dir)
        previous_page, next_page, missing_images = stage_chunk_sources(run_dir, output_dir, pages, chunk)
        write_json(
            run_dir / "chunk_task.json",
            {
                "version": 1,
                "chunk_id": chunk.chunk_id,
                "owned_pages": [page.number for page in chunk.pages],
                "owned_files": [page.path.name for page in chunk.pages],
                "previous_context_page": previous_page,
                "next_context_page": next_page,
                "missing_images": missing_images,
            },
        )
        print(f"启动转换 Agent {position}/{len(chunks)}：{chunk.chunk_id}", file=sys.stderr)
        conversion_prompt = chunk_prompt(chunk, previous_page, next_page)
        runner.run(run_dir, conversion_prompt)
        generated_tex = run_dir / "chunk.tex"
        report_path = run_dir / "chunk.report.json"
        validate_chunk_artifacts(generated_tex, report_path, chunk)
        audit = content_completeness(generated_tex, chunk)
        if not audit["passed"]:
            raise AgentPipelineError(
                f"转换分块完整度不足：{chunk.chunk_id}，"
                f"覆盖率 {audit['overall_coverage']:.1%}，缺失页 {audit['missing_pages']}"
            )
        report = read_json(report_path)
        report["completeness"] = audit
        write_json(report_path, report)
        shutil.copy2(run_dir / "chunk.tex", final_tex)
        shutil.copy2(report_path, final_report)
        print(f"完成转换 {position}/{len(chunks)}：{chunk.chunk_id}", file=sys.stderr)
        return chunk.chunk_id

    print(f"转换分块数：{len(chunks)}，并发数：{min(workers, len(chunks))}", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as executor:
        futures = [executor.submit(convert_one, item) for item in enumerate(chunks, start=1)]
        for future in as_completed(futures):
            future.result()
    return chunks


def review_prompt(chunk: Chunk) -> str:
    return f"""Work as the independent OCR-to-TeX reviewer described in AGENTS.md.

Inspect source/doc_N.md for pages {[page.number for page in chunk.pages]},
candidate.tex, candidate.report.json, and neighboring context pages declared
in chunk_task.json.

Review structure, completeness, page-boundary joins, formulas, figure
association, captions, and TeX syntax. Do not translate or correct OCR source
content. Do not edit source/. Correct TeX mapping errors in reviewed.tex.
Specifically reject raw HTML tables, `\\caption{{Image}}`, images wider than the
text block, and long prose paragraphs wrapped in `\\text{{...}}`; rewrite them
as ordinary TeX prose, width-constrained tables, and source-derived captions.
Do not add a root document wrapper or bibliography.

Write review.report.json:
{{
  "version": 1,
  "chunk_id": "{chunk.chunk_id}",
  "source_pages": {[page.number for page in chunk.pages]},
  "findings": [{{"severity": "P1", "page": 0, "issue": "...", "resolution": "..."}}],
  "status": "approved_or_revised"
}}

Finish only after reviewed.tex and valid review.report.json exist.
"""


def run_review(
    runner: CodexRunner,
    pages: Sequence[SourcePage],
    chunks: Sequence[Chunk],
    output_dir: Path,
    work_root: Path,
    template_dir: Path,
    overwrite: bool,
    workers: int,
) -> None:
    reviews_dir = work_root / "reviews"
    backups_dir = work_root / "pre_review"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    def review_one(item: tuple[int, Chunk]) -> str:
        position, chunk = item
        chapter_path = output_dir / "chapters" / f"{chunk.chunk_id}.tex"
        conversion_report = work_root / "reports" / f"{chunk.chunk_id}.json"
        review_report = reviews_dir / f"{chunk.chunk_id}.json"
        if not chapter_path.is_file() or not conversion_report.is_file():
            raise AgentPipelineError(f"审校前缺少转换产物：{chunk.chunk_id}")
        if review_report.is_file() and not overwrite:
            print(f"跳过已有审校 {position}/{len(chunks)}：{chunk.chunk_id}", file=sys.stderr)
            return chunk.chunk_id
        run_dir = timestamped_run_dir(work_root, f"review-{chunk.chunk_id}")
        copy_common_agent_context(run_dir, template_dir)
        previous_page, next_page, missing_images = stage_chunk_sources(run_dir, output_dir, pages, chunk)
        shutil.copy2(chapter_path, run_dir / "candidate.tex")
        shutil.copy2(conversion_report, run_dir / "candidate.report.json")
        write_json(
            run_dir / "chunk_task.json",
            {
                "chunk_id": chunk.chunk_id,
                "owned_pages": [page.number for page in chunk.pages],
                "previous_context_page": previous_page,
                "next_context_page": next_page,
                "missing_images": missing_images,
            },
        )
        print(f"启动审校 Agent {position}/{len(chunks)}：{chunk.chunk_id}", file=sys.stderr)
        runner.run(run_dir, review_prompt(chunk))
        review = read_json(run_dir / "review.report.json")
        if review.get("chunk_id") != chunk.chunk_id or review.get("source_pages") != [page.number for page in chunk.pages]:
            raise AgentPipelineError(f"审校报告与分块不匹配：{chunk.chunk_id}")
        reviewed_tex = run_dir / "reviewed.tex"
        if not reviewed_tex.is_file() or not reviewed_tex.read_text(encoding="utf-8", errors="replace").strip():
            raise AgentPipelineError(f"审校 Agent 未生成 reviewed.tex：{chunk.chunk_id}")
        reviewed_text = reviewed_tex.read_text(encoding="utf-8", errors="replace")
        if any(value in reviewed_text for value in ("\\documentclass", "\\begin{document}", "\\end{document}", "\\bibliography")):
            raise AgentPipelineError(f"审校 TeX 包含根文档命令：{chunk.chunk_id}")
        backup = backups_dir / f"{chunk.chunk_id}.tex"
        if not backup.exists():
            shutil.copy2(chapter_path, backup)
        shutil.copy2(reviewed_tex, chapter_path)
        shutil.copy2(run_dir / "review.report.json", review_report)
        print(f"完成审校 {position}/{len(chunks)}：{chunk.chunk_id}", file=sys.stderr)
        return chunk.chunk_id

    print(f"审校分块数：{len(chunks)}，并发数：{min(workers, len(chunks))}", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as executor:
        futures = [executor.submit(review_one, item) for item in enumerate(chunks, start=1)]
        for future in as_completed(futures):
            future.result()


def chunks_from_index(path: Path, pages: Sequence[SourcePage]) -> list[Chunk]:
    payload = read_json(path)
    entries = payload.get("chunks")
    if not isinstance(entries, list):
        raise AgentPipelineError(f"chunks.json 缺少 chunks 数组：{path}")
    page_map = {page.number: page for page in pages}
    chunks: list[Chunk] = []
    for entry in entries:
        numbers = entry.get("pages") if isinstance(entry, dict) else None
        if not isinstance(numbers, list) or any(number not in page_map for number in numbers):
            raise AgentPipelineError(f"chunks.json 包含无效页面：{entry}")
        chapter_number = entry.get("chapter_number")
        chapter_title = entry.get("chapter_title")
        section_titles = entry.get("section_titles")
        chunks.append(
            Chunk(
                str(entry["chunk_id"]),
                tuple(page_map[number] for number in numbers),
                chapter_number if isinstance(chapter_number, int) else None,
                chapter_title if isinstance(chapter_title, str) else None,
                tuple(value for value in section_titles if isinstance(value, str))
                if isinstance(section_titles, list)
                else (),
            )
        )
    return chunks


def tex_escape(value: str) -> str:
    replacements = {
        "\\": "\\textbackslash{}",
        "&": "\\&",
        "%": "\\%",
        "#": "\\#",
        "_": "\\_",
        "{": "\\{",
        "}": "\\}",
    }
    return "".join(replacements.get(character, character) for character in value)


def stage_template(output_dir: Path, template_dir: Path) -> list[str]:
    contract = read_json(template_dir / "template.json")
    staged: list[str] = []
    for relative in contract.get("assets", []):
        source = template_dir / str(relative)
        if not source.is_file():
            raise AgentPipelineError(f"模板资源不存在：{source}")
        shutil.copy2(source, output_dir / source.name)
        staged.append(source.name)
    return staged


def existing_chapter_artifacts(
    output_dir: Path,
    work_root: Path,
    manifest: dict[str, Any],
    pages: Sequence[SourcePage],
) -> tuple[list[str], list[dict[str, Any]], list[str]] | None:
    """Reuse complete chapter_NN.tex artifacts produced by an older pipeline."""
    outline_path = work_root / "chapter_manifest.json"
    if not outline_path.is_file():
        return None
    outline = read_json(outline_path)
    validate_chapter_manifest(outline, manifest, pages)
    input_lines: list[str] = []
    chapter_ranges: list[dict[str, Any]] = []
    chapter_reports: list[str] = []
    for chapter in outline["chapters"]:
        number = chapter["number"]
        chapter_id = f"chapter_{number:02d}"
        chapter_path = output_dir / "chapters" / f"{chapter_id}.tex"
        if not chapter_path.is_file():
            return None
        chapter_tex = chapter_path.read_text(encoding="utf-8", errors="replace")
        if not chapter_tex.strip():
            raise AgentPipelineError(f"已有章节文件为空：{chapter_path}")
        if not re.search(r"\\chapter\*?\s*\{", chapter_tex):
            input_lines.append(f"\\chapter{{{tex_escape(chapter['title'])}}}")
        input_lines.append(f"\\input{{chapters/{chapter_id}}}")
        chapter_ranges.append(
            {
                "number": number,
                "title": chapter["title"],
                "start_page": chapter["ocr_start_page"],
                "end_page": chapter["ocr_end_page"],
                "parts": 1,
            }
        )
        report_path = work_root / "chapter-reports" / f"{chapter_id}.json"
        if report_path.is_file():
            chapter_reports.append(str(report_path.relative_to(output_dir)))
    return input_lines, chapter_ranges, chapter_reports


def assemble_book(
    output_dir: Path,
    work_root: Path,
    template_dir: Path,
    manifest: dict[str, Any],
    chunks: Sequence[Chunk],
    pages: Sequence[SourcePage],
    title_override: str | None,
) -> Path:
    contract = read_json(template_dir / "template.json")
    staged_assets = stage_template(output_dir, template_dir)
    title = tex_escape(title_override or str(manifest.get("title") or "OCR Document"))
    author = tex_escape(str(manifest.get("author") or ""))
    input_lines: list[str] = []
    assembled_chapters: list[dict[str, Any]] = []
    chapter_reports: list[str] = []
    use_chunk_artifacts = bool(chunks) and all(
        chunk.chapter_number is not None and chunk.chapter_title for chunk in chunks
    )
    if use_chunk_artifacts:
        grouped: dict[int, list[Chunk]] = {}
        titles: dict[int, str] = {}
        for chunk in chunks:
            chunk_path = output_dir / "chapters" / f"{chunk.chunk_id}.tex"
            if not chunk_path.is_file():
                raise AgentPipelineError(f"缺少转换分块：{chunk_path}")
            report_path = work_root / "reports" / f"{chunk.chunk_id}.json"
            validate_chunk_artifacts(chunk_path, report_path, chunk)
            assert chunk.chapter_number is not None and chunk.chapter_title is not None
            grouped.setdefault(chunk.chapter_number, []).append(chunk)
            titles[chunk.chapter_number] = chunk.chapter_title
        expected_chapters = list(range(1, len(grouped) + 1))
        if sorted(grouped) != expected_chapters:
            raise AgentPipelineError(f"组装章节不完整：期望 {expected_chapters}，实际 {sorted(grouped)}")
        for number in expected_chapters:
            chapter_chunks = grouped[number]
            input_lines.append(f"\\chapter{{{tex_escape(titles[number])}}}")
            input_lines.extend(
                f"\\input{{chapters/{chunk.chunk_id}}}" for chunk in chapter_chunks
            )
            assembled_chapters.append(
                {
                    "number": number,
                    "title": titles[number],
                    "start_page": min(page.number for chunk in chapter_chunks for page in chunk.pages),
                    "end_page": max(page.number for chunk in chapter_chunks for page in chunk.pages),
                    "parts": len(chapter_chunks),
                }
            )
    else:
        existing = existing_chapter_artifacts(output_dir, work_root, manifest, pages)
        if existing is None:
            raise AgentPipelineError(
                "没有可组装的章节产物；缺少新版 chunk 归属和旧版 chapter_NN.tex"
            )
        input_lines, assembled_chapters, chapter_reports = existing
        print("复用已有 chapter_NN.tex，不重新运行转换 Agent", file=sys.stderr)
    inputs = "\n".join(input_lines)
    author_line = f"\\author{{{author}}}\n" if author else ""
    tex = (
        "% Root document assembled from Agent-produced TeX chunks.\n"
        f"{contract.get('document_class_line', '\\documentclass{article}')}\n"
        f"\\input{{{contract.get('preamble_input', 'preamble')}}}\n"
        f"\\title{{{title}}}\n"
        f"{author_line}"
        "\\begin{document}\n"
        "\\maketitle\n"
        "\\frontmatter\n"
        "\\tableofcontents\n"
        "\\mainmatter\n"
        f"{inputs}\n"
        "\\end{document}\n"
    )
    book_path = output_dir / "book.tex"
    book_path.write_text(tex, encoding="utf-8", newline="")
    reports = []
    review_reports = []
    completeness_reports: list[dict[str, Any]] = []
    for chunk in chunks if use_chunk_artifacts else ():
        report = read_json(work_root / "reports" / f"{chunk.chunk_id}.json")
        if isinstance(report.get("completeness"), dict):
            completeness_reports.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "passed": bool(report["completeness"].get("passed")),
                    "overall_coverage": report["completeness"].get("overall_coverage"),
                    "missing_pages": report["completeness"].get("missing_pages", []),
                }
            )
        reports.append(str((work_root / "reports" / f"{chunk.chunk_id}.json").relative_to(output_dir)))
        review_path = work_root / "reviews" / f"{chunk.chunk_id}.json"
        if review_path.is_file():
            review_reports.append(str(review_path.relative_to(output_dir)))
    write_json(
        output_dir / "document.report.json",
        {
            "version": 1,
            "source": "PaddleOCR doc_N.md",
            "page_manifest": str((work_root / "page_manifest.json").relative_to(output_dir)),
            "chapter_manifest": (
                str((work_root / "chapter_manifest.json").relative_to(output_dir))
                if (work_root / "chapter_manifest.json").is_file()
                else None
            ),
            "chunks": len(chunks) if use_chunk_artifacts else 0,
            "chapters": len(assembled_chapters),
            "chapter_ranges": assembled_chapters,
            "chunk_reports": reports,
            "chapter_reports": chapter_reports,
            "reviewed_chunks": len(review_reports),
            "review_reports": review_reports,
            "template_assets": staged_assets,
            "book_tex": book_path.name,
            "translation_stage": "optional",
            "conversion_mode": "chapter-aware" if use_chunk_artifacts else "existing-chapter-artifacts",
            "completeness": completeness_reports,
            "language_policy": "preserve_source_language",
        },
    )
    print(f"TeX 根文档：{book_path}", file=sys.stderr)
    return book_path


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用 Codex Agent 将 PaddleOCR 页面转换为保留源语言的 TeX")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="包含 doc_N.md 和 imgs/ 的 OCR 输出目录")
    parser.add_argument(
        "--phase",
        choices=["classify", "outline", "convert", "review", "assemble", "all", "full"],
        default="all",
    )
    parser.add_argument("--page-count", type=int, help="仅处理 doc_0.md 到 doc_<N-1>.md，避免旧 OCR 残留页混入")
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    parser.add_argument("--chunk-chars", type=int, default=45000, help="每个转换 Agent 负责的 OCR 字符量")
    parser.add_argument("--title", help="覆盖 Agent 推断的书名")
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="转换和审校阶段的并发 Agent 数量，默认 3；页面边界检测不调用 Agent",
    )
    parser.add_argument("--model", default=os.environ.get("MATH_TRANSLATOR_MODEL"))
    parser.add_argument("--reasoning", choices=["low", "medium", "high", "xhigh", "max"], default=os.environ.get("MATH_TRANSLATOR_REASONING"))
    parser.add_argument("--codex-bin", default=os.environ.get("MATH_TRANSLATOR_CODEX_BIN", "codex"))
    parser.add_argument("--timeout", type=int, default=1200, help="单个 Agent 运行超时秒数")
    parser.add_argument("--overwrite", action="store_true", help="重新运行已有转换或审校分块")
    parser.add_argument(
        "--dangerously-bypass-approvals-and-sandbox",
        dest="dangerously_bypass",
        action="store_true",
        help="无人值守运行 Codex（默认启用，保留此参数用于兼容显式调用）",
    )
    parser.add_argument(
        "--safe-agent",
        dest="dangerously_bypass",
        action="store_false",
        help="使用 workspace-write 沙箱，可能需要人工审批",
    )
    parser.set_defaults(dangerously_bypass=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        if args.workers < 1:
            raise AgentPipelineError("--workers 必须是正整数")
        if args.chunk_chars < 1:
            raise AgentPipelineError("--chunk-chars 必须是正整数")
        output_dir = Path(args.output_dir).expanduser().resolve()
        template_dir = Path(args.template_dir).expanduser().resolve()
        work_root = output_dir / "agent-work"
        work_root.mkdir(parents=True, exist_ok=True)
        pages = discover_pages(output_dir, args.page_count)
        manifest_path = work_root / "page_manifest.json"
        phase = args.phase
        needs_runner = phase in {"outline", "convert", "review", "all", "full"}
        runner = (
            CodexRunner(args.codex_bin, args.model, args.reasoning, args.timeout, args.dangerously_bypass)
            if needs_runner
            else None
        )
        if phase in {"classify", "outline", "convert", "all", "full"}:
            run_classification(pages, work_root, args.overwrite)
        if phase == "classify":
            return 0
        manifest = load_manifest(manifest_path, pages)
        if phase == "outline":
            assert runner is not None
            run_outline_agent(
                runner,
                pages,
                manifest,
                work_root,
                template_dir,
                args.overwrite,
            )
            return 0
        chunks_path = work_root / "chunks.json"
        if phase in {"convert", "all", "full"}:
            assert runner is not None
            chunks = run_conversion(
                runner,
                pages,
                manifest,
                output_dir,
                work_root,
                template_dir,
                args.chunk_chars,
                args.overwrite,
                args.workers,
                args.overwrite and phase in {"all", "full"},
            )
        else:
            chunks = chunks_from_index(chunks_path, pages)
        if phase in {"review", "full"}:
            assert runner is not None
            run_review(
                runner,
                pages,
                chunks,
                output_dir,
                work_root,
                template_dir,
                args.overwrite,
                args.workers,
            )
        if phase in {"assemble", "all", "full"}:
            assemble_book(
                output_dir,
                work_root,
                template_dir,
                manifest,
                chunks,
                pages,
                args.title,
            )
        return 0
    except (AgentPipelineError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
