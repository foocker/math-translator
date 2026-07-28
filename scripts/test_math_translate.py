from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from math_translate import (
    Glossary,
    TranslationError,
    approve_term,
    chunk_markdown,
    default_output_path,
    make_parser,
    protect_markdown,
    source_format_for,
    update_candidates,
)


class MathTranslatorTests(unittest.TestCase):
    def make_glossary(self, directory: Path) -> Path:
        path = directory / "glossary.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "terms": [
                        {
                            "source": "field",
                            "target": "域",
                            "domains": ["abstract_algebra"],
                            "aliases": ["fields"],
                            "status": "approved",
                        },
                        {
                            "source": "fraction field",
                            "target": "分式域",
                            "domains": ["abstract_algebra"],
                            "aliases": ["field of fractions"],
                            "status": "approved",
                        },
                        {
                            "source": "vector space",
                            "target": "向量空间",
                            "domains": ["*"],
                            "aliases": ["vector spaces"],
                            "status": "approved",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_protect_and_restore_markdown(self) -> None:
        source = """---
title: Test
---
# A $x^2$ result

See [proof](https://example.com/a?q=1) and `x = 1`.

```python
print("do not translate")
```
"""
        protected = protect_markdown(source)
        self.assertNotIn("x^2", protected.text)
        self.assertNotIn("https://example.com", protected.text)
        self.assertNotIn("print", protected.text)
        self.assertEqual(protected.restore(protected.text), source)

    def test_restore_rejects_missing_token(self) -> None:
        protected = protect_markdown("Value is $x$.")
        with self.assertRaises(TranslationError):
            protected.restore("值。")

    def test_longest_term_match_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            glossary = Glossary(self.make_glossary(Path(directory)), "abstract_algebra")
            matches = glossary.find("The fraction field is a field.")
        self.assertEqual([item.term.source for item in matches], ["fraction field", "field"])

    def test_domain_specific_terms_require_domain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_glossary(Path(directory))
            glossary = Glossary(path)
            matches = glossary.find("A field and a vector space.")
        self.assertEqual([item.term.source for item in matches], ["vector space"])

    def test_alias_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            glossary = Glossary(self.make_glossary(Path(directory)), "abstract_algebra")
            matches = glossary.find("Use the field of fractions.")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].term.source, "fraction field")

    def test_term_table_adds_conservative_supplemental_phrases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            table = root / "terms.tsv"
            table.write_text(
                "English\t中文\nlocally compact group\t局部紧群\nring\t环\n",
                encoding="utf-8",
            )
            glossary = Glossary(self.make_glossary(root), term_tables=[table])
            matches = glossary.find("A locally compact group acts on a ring.")
        self.assertEqual([item.term.source for item in matches], ["locally compact group"])

    def test_protects_latex_code_and_reference_commands(self) -> None:
        source = r"""\section{Main theorem}
See \cite{serre1977} and \input{chapters/intro}.
\begin{minted}{python}
print("do not translate")
\end{minted}
"""
        protected = protect_markdown(source)
        self.assertNotIn("serre1977", protected.text)
        self.assertNotIn("chapters/intro", protected.text)
        self.assertNotIn("do not translate", protected.text)
        self.assertEqual(protected.restore(protected.text), source)

    def test_conflicting_active_terms_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_glossary(Path(directory))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["terms"].append(
                {
                    "source": "field",
                    "target": "场",
                    "domains": ["abstract_algebra"],
                    "aliases": [],
                    "status": "approved",
                }
            )
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(TranslationError):
                Glossary(path, "abstract_algebra")

    def test_chunks_round_trip(self) -> None:
        source = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunk_markdown(source, 1000)
        self.assertEqual("".join(chunks), source)

    def test_candidates_accumulate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            value = {"source": "derived category", "target": "导出范畴", "domain": "algebra", "reason": "term"}
            update_candidates(path, [value])
            update_candidates(path, [value])
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["candidates"][0]["occurrences"], 2)

    def test_approve_adds_term(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_glossary(Path(directory))
            approve_term(path, "derived category", "导出范畴", "algebraic_geometry", "manual")
            glossary = Glossary(path, "algebraic_geometry")
            matches = glossary.find("A derived category.")
        self.assertEqual(matches[0].term.target, "导出范畴")

    def test_codex_model_and_reasoning_default_to_local_config(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            args = make_parser().parse_args(["input.md"])
        self.assertIsNone(args.model)
        self.assertIsNone(args.reasoning)

    def test_tex_output_defaults_and_format_detection(self) -> None:
        path = Path("book/chapter1.tex")
        self.assertEqual(default_output_path(path), Path("book/chapter1.zh.tex"))
        self.assertEqual(source_format_for(path), "LaTeX")


if __name__ == "__main__":
    unittest.main()
