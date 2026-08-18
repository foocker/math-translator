# Simple OCR Translation Agent Contract

You convert one staged Markdown chunk from an English mathematics book into a
Simplified Chinese ElegantBook TeX body fragment.

## Required Work

- Read `task.json` and `source.md`.
- Translate visible English prose into accurate, natural Simplified Chinese.
- Translate every paragraph in full and in source order. Do not summarize,
  condense, omit, or merge source content.
- Map Markdown headings, paragraphs, lists, tables, quotations, and existing
  mathematics to valid TeX body structure.
- Preserve source order and page-boundary continuity.
- Write only the files requested by the task prompt.

## File Encoding

- Every task file is UTF-8 with BOM where needed for Windows PowerShell 5.1.
- Use `apply_patch` for edits to `chunk.zh.tex`; replace its existing
  placeholder without deleting or recreating the file.
- If PowerShell must read or write file contents, always pass
  `-Encoding utf8` to `Get-Content`, `Set-Content`, `Add-Content`, and
  `Out-File`.
- Never read and rewrite the entire TeX file merely to normalize slashes,
  line endings, or encoding. Write valid single TeX command backslashes in the
  first place.
- Before finishing, reject output containing mojibake, replacement characters,
  private-use characters, or unpaired math delimiters.

## Deliberate Non-Goals

- Do not repair OCR errors, mojibake, notation, formulas, names, punctuation,
  missing text, or malformed source. Preserve uncertain OCR content rather than
  guessing.
- Do not process, translate, describe, redraw, or emit images or figure floats.
- The pipeline copies original images and creates companion TeX float files
  mechanically after translation; image changes must not cause retranslation.
- Do not perform an independent review pass.
- Do not add bibliography or content outside the staged pages.

## TeX Boundary

- Preserve every inline and display formula exactly as supplied whenever it is
  already TeX-like. Never translate mathematical commands or notation.
- Translate reader-facing headings and prose, but do not translate file names,
  labels, command names, or structural identifiers.
- Emit a body fragment only. Do not emit `\documentclass`, a preamble,
  `\begin{document}`, `\end{document}`, `\maketitle`, or
  `\tableofcontents`.
- Do not wrap TeX in Markdown code fences.
- Lines such as `<!-- SOURCE_PAGE doc_10.md -->` are provenance markers and
  must not appear in the TeX output.
