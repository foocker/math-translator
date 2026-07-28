---
name: pure-math-translator
description: Translate pure mathematics papers, lecture notes, and book chapters from English to Chinese. Supports local Markdown, local LaTeX, LaTeX projects, and arXiv source while preserving formulas, theorem/proof structure, references, and compilability.
license: MIT
---

# Pure Math Translator

Use this skill when the user wants to translate pure mathematics writing into Chinese: research papers, arXiv papers, lecture notes, monographs, book chapters, problem sets, or expository mathematical essays.

The goal is not only readable Chinese. The translation must preserve mathematical meaning: hypotheses, quantifiers, negations, dependencies, theorem/proof boundaries, labels, citations, formulas, and cross-references.

## Core Inheritance

This skill keeps the useful experience from `arxiv-paper-translator`:

1. Gather document context before translating.
2. Build a shared terminology table before dispatching work.
3. Translate the main/root file first when a LaTeX project has multiple files.
4. Translate remaining files in parallel only after shared context is stable.
5. Run a mandatory review phase before declaring completion.
6. Add CJK support and compile translated TeX when the user wants a PDF.
7. Produce a short translation report when useful.

It adapts those rules for pure mathematics and books:

1. Treat theorem/proof/definition/lemma/corollary/proposition/exercise environments as first-class structure.
2. Preserve formulas and proof logic more strictly than general academic prose.
3. Support `.md` and `.tex` inputs, not only arXiv source.
4. Support long book chapters or lecture notes where chapter/section consistency matters.
5. Use the embedded math glossary and the supplemental English-Chinese mathematical TSV dictionary as hard terminology constraints, but never load the full dictionary into model context.

## Required References

Before translating, read:

- [references/translation_guidelines.md](references/translation_guidelines.md)
- [references/translation_prompt.md](references/translation_prompt.md)
- [references/review_checklist.md](references/review_checklist.md)

If compiling TeX or preparing a translated TeX project, also read:

- [references/chinese_support.md](references/chinese_support.md)

## Input Routing

### Local Single Markdown or TeX File

Use the script backend first. The script automatically searches the embedded glossary and TSV dictionary against the source, then passes only matched terminology to the model as hard constraints:

```powershell
python tools/pure-math-translator/scripts/math_translate.py INPUT_FILE --document-kind paper --domain DOMAIN
```

For books or chapters:

```powershell
python tools/pure-math-translator/scripts/math_translate.py INPUT_FILE --document-kind book --domain DOMAIN
```

Before large translations, run terminology extraction. This is the dictionary retrieval layer: it scans the source files and produces a small matched terminology report, avoiding context pollution from the full dictionary:

```powershell
python tools/pure-math-translator/scripts/extract_terms.py INPUT_FILE --domain DOMAIN -o matched_terms.md
```

Then use `--dry-run` to inspect protected segments and per-file terminology matches. If a key mathematical term is missing or matched wrongly, update `glossary.json` or pass a narrower `--domain` before translating:

```powershell
python tools/pure-math-translator/scripts/math_translate.py INPUT_FILE --document-kind book --domain DOMAIN --dry-run
```

### Local LaTeX Project

1. Copy the whole source project into a translated output directory, usually `<source>_zh/` or `paper_cn/`.
2. Identify the main TeX file: the file containing `\documentclass`.
3. Extract context from the main file and included section files.
4. Translate main/root file first.
5. Translate included section files next.
6. Keep figures, bibliography files, style files, class files, and build scripts unless visible English text must be localized.
7. Run review checks.
8. Add CJK support and compile if requested.

### arXiv URL Or ID

Preserve the automatic arXiv workflow from `arxiv-paper-translator`: the user may provide an arXiv ID, an `abs` URL, a `pdf` URL, or an `e-print` URL. First normalize it and download the TeX source package:

```powershell
python tools/pure-math-translator/scripts/fetch_arxiv_source.py "https://arxiv.org/abs/2206.04655"
```

The script creates:

```text
arXiv_<ID>/
  paper_source.tar.gz
  paper_source/
```

It handles normal tar archives, single gzipped TeX files, and raw single TeX responses. After extraction, process `paper_source/` as a local LaTeX project.

Use `--print-id` when you only need to verify URL parsing:

```powershell
python tools/pure-math-translator/scripts/fetch_arxiv_source.py "https://arxiv.org/pdf/2206.04655" --print-id
```

## Context Extraction

Before translation, extract and write down:

1. Title and subtitle, if present.
2. Authors only for context; do not translate personal names unless there is a standard Chinese name.
3. Abstract, introduction, preface, or chapter opening.
4. Document structure: chapters, sections, subsections, theorem-like blocks, appendices, exercises.
5. Mathematical area: e.g. algebraic geometry, topology, number theory, functional analysis, probability, category theory.
6. Key terminology table: English -> Chinese, English -> keep, and ambiguous terms needing review.
7. Notation conventions that affect wording: sheaves, schemes, fields, rings, measures, categories, functors, spectra, manifolds, varieties, etc.

If a key term has multiple established Chinese translations, prefer the user's choice. If unavailable, use the glossary and record uncertainty in the review notes.

## Translation Strategy

For single files, prefer the local script because it protects formulas, code, links, references, paths, and structure before invoking Codex, and because it injects matched dictionary terminology as hard constraints:

```powershell
python tools/pure-math-translator/scripts/math_translate.py INPUT -o OUTPUT --document-kind book --domain topology
```

For multi-file projects:

1. Translate the main file first using the prompt template.
2. Maintain one shared terminology table for all files.
3. Translate section/chapter files in parallel only after the terminology table is stable.
4. Do not translate bibliography entries unless the user explicitly requests bibliography title translation.
5. Do not translate code, formulas, raw data tables, labels, citation keys, file paths, or macro names.
6. Translate visible reader-facing text in captions, theorem names, section titles, footnotes, prefaces, exercises, and remarks.

## Mandatory Review

Before final response, complete the review checklist:

1. File completeness.
2. Structure preservation.
3. LaTeX command spelling and CJK catcode scan.
4. Formula/reference/label preservation.
5. Terminology consistency.
6. Theorem/proof logic spot-check.
7. Chinese mathematical prose quality.
8. Compile check if a PDF is requested and TeX tooling is available.

If a compile step is impossible because XeLaTeX/Docker is missing, report that clearly and still provide the translated source.

## Final Deliverables

For a single file:

- Translated file, usually `<name>.zh.md` or `<name>.zh.tex`.
- Candidate terminology updates in `tools/pure-math-translator/candidates.json`, unless `--no-learn` was used.

For a TeX project or arXiv paper:

- Translated source directory.
- Compiled PDF if requested and tooling is available.
- Brief review/translation notes with unresolved terminology questions.

For a book or long lecture notes:

- Translated chapter files.
- Shared terminology table or notes when terminology consistency matters.
