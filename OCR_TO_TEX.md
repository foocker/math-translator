# PaddleOCR to Agent-Produced TeX

The PaddleOCR request flow remains in `scripts/ppocr-vl.py`. It writes raw
`doc_N.md`, `imgs/`, and layout images to `tools/math-translator/output`, then
starts the separate Codex orchestration module.

Before running OCR, create `.env` from `.env.example` and set the API token:

```powershell
Copy-Item .env.example .env
# Edit .env and set PADDLEOCR_TOKEN
```

The script loads `.env` relative to the project directory, so it also works
when launched from another current directory. An existing `PADDLEOCR_TOKEN`
environment variable takes precedence over `.env`.

## End-to-end

From `tools/math-translator`:

```powershell
python scripts/ppocr-vl.py "test_resources/book.pdf" --output-dir output/book-test
python scripts/ppocr-vl.py "https://example.com/book.pdf" --output-dir output/url-test
```

`--output-dir` accepts relative or absolute paths and creates missing parent
directories automatically. Use a separate directory per input to avoid
overwriting an existing OCR run. It is also passed to the TeX Agent.

Use `--ocr-only` to stop after the original OCR output is saved:

```powershell
python scripts/ppocr-vl.py "test_resources/book.pdf" --output-dir output/book-test --ocr-only
```

## Agent stages

Run against existing OCR output without another OCR request:

```powershell
python scripts/ocr_to_tex_agent.py --phase classify
python scripts/ocr_to_tex_agent.py --phase convert
python scripts/ocr_to_tex_agent.py --phase review
python scripts/ocr_to_tex_agent.py --phase assemble
```

`--phase all` runs classify, convert, and assemble. `--phase full` also runs the
independent review agents. Completed chunks are resumable; pass `--overwrite`
to rerun them.

Agent phases use `--dangerously-bypass-approvals-and-sandbox` internally by
default for unattended conversion. The OCR source is still protected because
each Agent runs on staged copies under `agent-work/runs/`; pass `--safe-agent`
only when sandboxed, potentially interactive execution is preferred.

The stages are:

- `classify`: an Agent inspects pages and writes `agent-work/page_manifest.json`.
- `convert`: isolated Agents write `chapters/chunk_N.tex` plus repair reports.
- `review`: independent Agents compare each chunk with its OCR evidence.
- `assemble`: Python only copies fixed template assets and wires Agent-produced
  chunks into `book.tex`.

Original `doc_N.md` files are never given to an Agent as writable working
files. Each run uses staged copies under `output/agent-work/runs/`.

OCR repair knowledge is maintained in `config/ocr_correction_rules.json`.
Rules are contextual guidance, never automatic substitutions. Repairs and
uncertainties are recorded per chunk for later evaluation.

The resulting source-language TeX chunks are the input boundary for an optional
later translation stage. OCR-to-TeX itself never translates prose.
