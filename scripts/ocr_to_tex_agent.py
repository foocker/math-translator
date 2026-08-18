#!/usr/bin/env python3
"""Orchestrate Codex agents that turn PaddleOCR page Markdown into TeX.

This module never edits ``doc_N.md``. It stages read-only copies for isolated
Codex runs, imports validated agent artifacts, and performs only final TeX
assembly in Python. Page classification, Markdown interpretation, semantic OCR
repair, figure association, and review belong to the agents.
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
DEFAULT_RULES = TOOL_DIR / "config" / "ocr_correction_rules.json"
PAGE_PATTERN = re.compile(r"doc_(\d+)\.md", re.IGNORECASE)
ALLOWED_PAGE_CLASSES = {
    "front_matter",
    "contents",
    "contents_continuation",
    "body",
    "bibliography",
    "back_matter",
}


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


def source_language_counts(pages: Sequence[SourcePage]) -> tuple[int, int]:
    text = "\n".join(page.path.read_text(encoding="utf-8", errors="replace") for page in pages)
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    return cjk_count, latin_count


def source_language_profile(pages: Sequence[SourcePage]) -> str:
    cjk_count, latin_count = source_language_counts(pages)
    if cjk_count >= 20 and cjk_count >= latin_count:
        return "Chinese-dominant"
    if latin_count >= 20 and latin_count > cjk_count:
        return "English-dominant"
    return "Mixed-or-undetermined"


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
        value = json.loads(path.read_text(encoding="utf-8"))
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


def page_preview(page: SourcePage) -> dict[str, Any]:
    text = page.path.read_text(encoding="utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headings = [line[:160] for line in lines if re.match(r"^#{1,6}\s+", line)][:4]
    return {
        "page": page.number,
        "file": page.path.name,
        "characters": len(text),
        "headings": headings,
        "opening": "\n".join(lines[:3])[:240],
        "closing": "\n".join(lines[-2:])[-160:],
    }


def copy_common_agent_context(run_dir: Path, template_dir: Path, rules_path: Path) -> None:
    shutil.copy2(template_dir / "AGENTS.md", run_dir / "AGENTS.md")
    shutil.copy2(template_dir / "template.json", run_dir / "template.json")
    shutil.copy2(template_dir / "preamble.tex", run_dir / "preamble.tex")
    shutil.copy2(rules_path, run_dir / "ocr_correction_rules.json")


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


def classification_prompt(page_count: int) -> str:
    return f"""Work as the document classification agent described in AGENTS.md.

Read AGENTS.md, ocr_correction_rules.json, source_pages.json, and the staged
source/doc_N.md files. Classify every one of the {page_count} pages by semantic
role. Inspect the actual page files around every possible boundary; previews
are navigation aids, not evidence on their own.

Required behavior:
- Do not edit anything under source/.
- Locate the real Contents/Table of Contents/目录 page and all continuation
  pages. Those OCR table entries are not body prose.
- Body starts at the first genuine retained page after the complete contents,
  including a preface/introduction when it occurs after the contents.
- Locate the Bibliography/References/参考文献 heading. Its page is excluded,
  as are all later pages.
- Classify every page as exactly one of: front_matter, contents,
  contents_continuation, body, bibliography, back_matter.
- Use semantic judgment for blank pages and split headings. Do not classify by
  a blind keyword replacement script.

Write page_manifest.json with this exact shape:
{{
  "version": 1,
  "title": "inferred original-language title or OCR Document",
  "author": "inferred author or empty string",
  "contents_page": 0,
  "body_start_page": 0,
  "end_page_excluded": 0,
  "boundary_reasoning": {{"start": "...", "end": "..."}},
  "pages": [
    {{"page": 0, "file": "doc_0.md", "classification": "front_matter", "reason": "..."}}
  ],
  "uncertainties": []
}}

Use null for end_page_excluded only if no bibliography-like boundary exists.
The pages array must contain every source page exactly once and in numeric
order. Finish only after page_manifest.json is valid JSON.
"""


def validate_manifest(manifest: dict[str, Any], pages: Sequence[SourcePage]) -> None:
    entries = manifest.get("pages")
    if not isinstance(entries, list):
        raise AgentPipelineError("page_manifest.json 缺少 pages 数组")
    expected = [page.number for page in pages]
    expected_files = {page.number: page.path.name for page in pages}
    actual: list[int] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("page"), int):
            raise AgentPipelineError("page_manifest.json 包含无效页面项")
        if entry["page"] not in expected_files:
            raise AgentPipelineError(f"page_manifest.json 包含未知页面：{entry['page']}")
        classification = entry.get("classification")
        if classification not in ALLOWED_PAGE_CLASSES:
            raise AgentPipelineError(f"无效页面分类：{classification}")
        if entry.get("file") != expected_files[entry["page"]]:
            raise AgentPipelineError(f"页面文件名不匹配：{entry}")
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            raise AgentPipelineError(f"页面分类缺少判断理由：{entry}")
        actual.append(entry["page"])
    if actual != expected:
        raise AgentPipelineError("page_manifest.json 必须按顺序且不重不漏地包含全部 OCR 页面")
    body = [entry for entry in entries if entry["classification"] == "body"]
    contents = [entry for entry in entries if entry["classification"] == "contents"]
    if not contents or not body:
        raise AgentPipelineError("Agent 未识别到目录页或正文页")
    if manifest.get("contents_page") != contents[0]["page"]:
        raise AgentPipelineError("contents_page 与首个 contents 页面不一致")
    if manifest.get("body_start_page") != body[0]["page"]:
        raise AgentPipelineError("body_start_page 与首个 body 页面不一致")
    if body[0]["page"] <= contents[0]["page"]:
        raise AgentPipelineError("正文必须位于目录之后")
    end_page = manifest.get("end_page_excluded")
    if end_page is not None:
        if not isinstance(end_page, int) or end_page not in expected:
            raise AgentPipelineError("end_page_excluded 必须是源页面编号或 null")
        if any(entry["page"] >= end_page and entry["classification"] == "body" for entry in entries):
            raise AgentPipelineError("end_page_excluded 之后不能再有 body 页面")
        end_entry = next(entry for entry in entries if entry["page"] == end_page)
        if end_entry["classification"] != "bibliography":
            raise AgentPipelineError("end_page_excluded 必须指向 bibliography 页面")


def run_classification(
    runner: CodexRunner,
    pages: Sequence[SourcePage],
    output_dir: Path,
    work_root: Path,
    template_dir: Path,
    rules_path: Path,
) -> Path:
    run_dir = timestamped_run_dir(work_root, "classify")
    copy_common_agent_context(run_dir, template_dir, rules_path)
    for page in pages:
        copy_page(run_dir, page)
    write_json(run_dir / "source_pages.json", {"version": 1, "pages": [page_preview(page) for page in pages]})
    print(f"启动页面分类 Agent：{run_dir}", file=sys.stderr)
    runner.run(run_dir, classification_prompt(len(pages)))
    manifest = read_json(run_dir / "page_manifest.json")
    validate_manifest(manifest, pages)
    destination = work_root / "page_manifest.json"
    shutil.copy2(run_dir / "page_manifest.json", destination)
    print(f"页面分类清单：{destination}", file=sys.stderr)
    return destination


def load_manifest(path: Path, pages: Sequence[SourcePage]) -> dict[str, Any]:
    manifest = read_json(path)
    validate_manifest(manifest, pages)
    return manifest


def body_pages_from_manifest(manifest: dict[str, Any], pages: Sequence[SourcePage]) -> list[SourcePage]:
    body_numbers = {
        entry["page"]
        for entry in manifest["pages"]
        if entry.get("classification") == "body"
    }
    return [page for page in pages if page.number in body_numbers]


def make_chunks(pages: Sequence[SourcePage], max_chars: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[SourcePage] = []
    current_chars = 0
    for page in pages:
        if current and current_chars + page.chars > max_chars:
            chunks.append(Chunk(f"chunk_{len(chunks):04d}", tuple(current)))
            current = []
            current_chars = 0
        current.append(page)
        current_chars += page.chars
    if current:
        chunks.append(Chunk(f"chunk_{len(chunks):04d}", tuple(current)))
    return chunks


def chunk_prompt(
    chunk: Chunk,
    previous_page: int | None,
    next_page: int | None,
    language_profile: str,
) -> str:
    page_files = [page.path.name for page in chunk.pages]
    return f"""Work as the OCR-to-TeX conversion agent described in AGENTS.md.

Read AGENTS.md, ocr_correction_rules.json, template.json, preamble.tex, and
chunk_task.json. Convert only these owned OCR pages, in order:
{json.dumps(page_files, ensure_ascii=False)}

Neighbor page numbers are previous={previous_page!r}, next={next_page!r}. They
are context-only when present under source/; do not duplicate their content.
The local source-language profile is: {language_profile}. Treat it as a guardrail
for preserving language, not as permission to translate.

Required behavior:
- Do not edit anything under source/.
- Preserve the source language exactly. Chinese remains Chinese, English remains
  English, and mixed-language passages remain mixed-language. Do not translate
  prose, headings, captions, theorem names, proofs, or exercises.
- Produce TeX body fragments in the source language. Translation is an optional
  later stage and is not part of OCR-to-TeX conversion.
- Interpret Markdown semantically: preserve chapter/section hierarchy,
  theorem-like statements, proofs, exercises, lists, tables, inline/display
  mathematics, and page-boundary continuations.
- Repair OCR mistakes only with contextual evidence. Never apply global O/0,
  subscript, superscript, symbol, or punctuation substitutions.
- For each semantic repair, record page, original, corrected, rule_id, reason,
  and confidence. Preserve ambiguous text and record an uncertainty.
- Convert referenced images to TeX figures. Use minipages only when images are
  genuinely related and intended side by side. Final paths must remain relative
  to the book root, normally imgs/<name>.
- Do not emit documentclass, preamble, begin{{document}}, end{{document}}, title,
  maketitle, tableofcontents, bibliography, or references.

Write chunk.tex containing only the TeX body for the owned pages.
Write chunk.report.json with this exact shape:
{{
  "version": 1,
  "chunk_id": "{chunk.chunk_id}",
  "source_pages": {[page.number for page in chunk.pages]},
  "corrections": [
    {{"page": 0, "original": "...", "corrected": "...", "rule_id": "math-o-zero", "reason": "...", "confidence": "high"}}
  ],
  "uncertainties": [],
  "structure_notes": [],
  "image_notes": []
}}

Finish only after both files exist and chunk.report.json is valid JSON.
"""


def validate_chunk_artifacts(tex_path: Path, report_path: Path, chunk: Chunk) -> dict[str, Any]:
    if not tex_path.is_file() or not tex_path.read_text(encoding="utf-8", errors="replace").strip():
        raise AgentPipelineError(f"Agent 未生成有效 TeX：{tex_path}")
    tex = tex_path.read_text(encoding="utf-8", errors="replace")
    source_cjk, source_latin = source_language_counts(chunk.pages)
    if source_cjk >= 20 and source_cjk >= source_latin:
        target_cjk = len(re.findall(r"[\u3400-\u9fff]", tex))
        minimum_cjk = max(10, source_cjk // 10)
        if target_cjk < minimum_cjk:
            raise AgentPipelineError(
                f"源文档为中文主导，但 TeX 中文字符过少：{tex_path} "
                f"(source={source_cjk}, tex={target_cjk}, minimum={minimum_cjk})"
            )
    if tex.lstrip().startswith("```"):
        raise AgentPipelineError(f"TeX 分块不能包含 Markdown 代码围栏：{tex_path}")
    forbidden = ("\\documentclass", "\\begin{document}", "\\end{document}", "\\bibliography")
    if any(value in tex for value in forbidden):
        raise AgentPipelineError(f"TeX 分块包含根文档命令：{tex_path}")
    report = read_json(report_path)
    if report.get("chunk_id") != chunk.chunk_id:
        raise AgentPipelineError(f"chunk_id 不匹配：{report_path}")
    expected_pages = [page.number for page in chunk.pages]
    if report.get("source_pages") != expected_pages:
        raise AgentPipelineError(f"source_pages 不匹配：{report_path}")
    for key in ("corrections", "uncertainties", "structure_notes", "image_notes"):
        if not isinstance(report.get(key), list):
            raise AgentPipelineError(f"分块报告缺少数组 {key}：{report_path}")
    for correction in report["corrections"]:
        required = {"page", "original", "corrected", "rule_id", "reason", "confidence"}
        if not isinstance(correction, dict) or not required.issubset(correction):
            raise AgentPipelineError(f"分块报告包含不完整修复项：{report_path}")
        if correction["page"] not in expected_pages:
            raise AgentPipelineError(f"修复项引用了非本分块页面：{report_path}")
    return report


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
                }
                for chunk in chunks
            ],
        },
    )
    return path


def run_conversion(
    runner: CodexRunner,
    pages: Sequence[SourcePage],
    manifest: dict[str, Any],
    output_dir: Path,
    work_root: Path,
    template_dir: Path,
    rules_path: Path,
    chunk_chars: int,
    overwrite: bool,
    workers: int,
) -> list[Chunk]:
    body_pages = body_pages_from_manifest(manifest, pages)
    chunks = make_chunks(body_pages, chunk_chars)
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
            validate_chunk_artifacts(final_tex, final_report, chunk)
            print(f"跳过已有分块 {position}/{len(chunks)}：{chunk.chunk_id}", file=sys.stderr)
            return chunk.chunk_id
        run_dir = timestamped_run_dir(work_root, chunk.chunk_id)
        copy_common_agent_context(run_dir, template_dir, rules_path)
        previous_page, next_page, missing_images = stage_chunk_sources(run_dir, output_dir, pages, chunk)
        language_profile = source_language_profile(chunk.pages)
        write_json(
            run_dir / "chunk_task.json",
            {
                "version": 1,
                "chunk_id": chunk.chunk_id,
                "owned_pages": [page.number for page in chunk.pages],
                "owned_files": [page.path.name for page in chunk.pages],
                "previous_context_page": previous_page,
                "next_context_page": next_page,
                "source_language": language_profile,
                "missing_images": missing_images,
            },
        )
        print(f"启动转换 Agent {position}/{len(chunks)}：{chunk.chunk_id}", file=sys.stderr)
        runner.run(run_dir, chunk_prompt(chunk, previous_page, next_page, language_profile))
        validate_chunk_artifacts(run_dir / "chunk.tex", run_dir / "chunk.report.json", chunk)
        shutil.copy2(run_dir / "chunk.tex", final_tex)
        shutil.copy2(run_dir / "chunk.report.json", final_report)
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
candidate.tex, candidate.report.json, ocr_correction_rules.json, and neighboring
context pages declared in chunk_task.json.

Review structure, completeness, page-boundary joins, formulas, OCR repairs,
figure association, captions, and TeX syntax. Do not translate. Do not edit
source/. Correct candidate errors in reviewed.tex, preserving sound content.
Do not add a root document wrapper or bibliography.

Write review.report.json:
{{
  "version": 1,
  "chunk_id": "{chunk.chunk_id}",
  "source_pages": {[page.number for page in chunk.pages]},
  "findings": [{{"severity": "P1", "page": 0, "issue": "...", "resolution": "..."}}],
  "remaining_uncertainties": [],
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
    rules_path: Path,
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
        copy_common_agent_context(run_dir, template_dir, rules_path)
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
        chunks.append(Chunk(str(entry["chunk_id"]), tuple(page_map[number] for number in numbers)))
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


def stage_template(output_dir: Path, template_dir: Path, rules_path: Path) -> list[str]:
    contract = read_json(template_dir / "template.json")
    staged: list[str] = []
    for relative in contract.get("assets", []):
        source = template_dir / str(relative)
        if not source.is_file():
            raise AgentPipelineError(f"模板资源不存在：{source}")
        shutil.copy2(source, output_dir / source.name)
        staged.append(source.name)
    shutil.copy2(rules_path, output_dir / rules_path.name)
    staged.append(rules_path.name)
    return staged


def assemble_book(
    output_dir: Path,
    work_root: Path,
    template_dir: Path,
    rules_path: Path,
    manifest: dict[str, Any],
    chunks: Sequence[Chunk],
    title_override: str | None,
) -> Path:
    contract = read_json(template_dir / "template.json")
    staged_assets = stage_template(output_dir, template_dir, rules_path)
    title = tex_escape(title_override or str(manifest.get("title") or "OCR Document"))
    author = tex_escape(str(manifest.get("author") or ""))
    inputs = "\n".join(f"\\input{{chapters/{chunk.chunk_id}}}" for chunk in chunks)
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
    correction_count = 0
    corrections_by_rule: dict[str, int] = {}
    uncertainty_count = 0
    review_reports = []
    for chunk in chunks:
        report = read_json(work_root / "reports" / f"{chunk.chunk_id}.json")
        correction_count += len(report.get("corrections", []))
        for correction in report.get("corrections", []):
            rule_id = str(correction.get("rule_id", "unclassified"))
            corrections_by_rule[rule_id] = corrections_by_rule.get(rule_id, 0) + 1
        uncertainty_count += len(report.get("uncertainties", []))
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
            "chunks": len(chunks),
            "chunk_reports": reports,
            "semantic_corrections": correction_count,
            "corrections_by_rule": corrections_by_rule,
            "remaining_uncertainties": uncertainty_count,
            "reviewed_chunks": len(review_reports),
            "review_reports": review_reports,
            "template_assets": staged_assets,
            "book_tex": book_path.name,
            "translation_stage": "optional",
            "language_policy": "preserve_source_language",
        },
    )
    print(f"TeX 根文档：{book_path}", file=sys.stderr)
    return book_path


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用 Codex Agent 将 PaddleOCR 页面转换为保留源语言的 TeX")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="包含 doc_N.md 和 imgs/ 的 OCR 输出目录")
    parser.add_argument("--phase", choices=["classify", "convert", "review", "assemble", "all", "full"], default="all")
    parser.add_argument("--page-count", type=int, help="仅处理 doc_0.md 到 doc_<N-1>.md，避免旧 OCR 残留页混入")
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    parser.add_argument("--rules", default=str(DEFAULT_RULES), help="Agent OCR 修复知识表")
    parser.add_argument("--chunk-chars", type=int, default=45000, help="每个转换 Agent 负责的 OCR 字符量")
    parser.add_argument("--title", help="覆盖 Agent 推断的书名")
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="转换和审校阶段的并发 Agent 数量，默认 3；页面分类仍是单次整体调用",
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
        output_dir = Path(args.output_dir).expanduser().resolve()
        template_dir = Path(args.template_dir).expanduser().resolve()
        rules_path = Path(args.rules).expanduser().resolve()
        work_root = output_dir / "agent-work"
        work_root.mkdir(parents=True, exist_ok=True)
        pages = discover_pages(output_dir, args.page_count)
        manifest_path = work_root / "page_manifest.json"
        phase = args.phase
        runner = (
            CodexRunner(args.codex_bin, args.model, args.reasoning, args.timeout, args.dangerously_bypass)
            if phase != "assemble"
            else None
        )
        if phase in {"classify", "all", "full"}:
            assert runner is not None
            if args.overwrite or not manifest_path.is_file():
                run_classification(runner, pages, output_dir, work_root, template_dir, rules_path)
            else:
                load_manifest(manifest_path, pages)
                print(f"沿用页面分类清单：{manifest_path}", file=sys.stderr)
        if phase == "classify":
            return 0
        manifest = load_manifest(manifest_path, pages)
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
                rules_path,
                args.chunk_chars,
                args.overwrite,
                args.workers,
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
                rules_path,
                args.overwrite,
                args.workers,
            )
        if phase in {"assemble", "all", "full"}:
            assemble_book(output_dir, work_root, template_dir, rules_path, manifest, chunks, args.title)
        return 0
    except (AgentPipelineError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
