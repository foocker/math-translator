import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("ocr_to_tex_agent.py")
SPEC = importlib.util.spec_from_file_location("ocr_to_tex_agent", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
ocr_to_tex_agent = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ocr_to_tex_agent
SPEC.loader.exec_module(ocr_to_tex_agent)


class ExcerptedPdfOutlineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.pages = []
        for number in range(5, 13):
            path = root / f"doc_{number}.md"
            path.write_text(f"page {number}\n", encoding="utf-8")
            self.pages.append(ocr_to_tex_agent.SourcePage(number, path, path.stat().st_size))
        self.page_manifest = {
            "contents_page": 0,
            "body_start_page": 5,
            "end_page_excluded": None,
        }
        self.outline = {
            "chapters": [
                {
                    "number": 1,
                    "title": "Chapter One",
                    "printed_start_page": 1,
                    "ocr_start_page": 5,
                    "ocr_end_page": 8,
                    "sections": [],
                },
                {
                    "number": 2,
                    "title": "Missing Chapter",
                    "printed_start_page": 20,
                    "ocr_start_page": None,
                    "ocr_end_page": None,
                    "sections": [
                        {
                            "title": "Missing Section",
                            "printed_start_page": 20,
                            "ocr_start_page": None,
                        }
                    ],
                },
                {
                    "number": 3,
                    "title": "Chapter Three",
                    "printed_start_page": 40,
                    "ocr_start_page": 9,
                    "ocr_end_page": 12,
                    "sections": [],
                },
            ],
            "body_end": {
                "heading": "",
                "printed_start_page": None,
                "ocr_start_page": None,
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_toc_chapter_is_valid(self) -> None:
        ocr_to_tex_agent.validate_chapter_manifest(
            self.outline, self.page_manifest, self.pages
        )

    def test_chunk_planner_skips_missing_chapter_and_keeps_numbers(self) -> None:
        chunks = ocr_to_tex_agent.plan_chapter_chunks(
            self.outline, self.pages, max_chars=10_000
        )
        self.assertEqual([chunk.chapter_number for chunk in chunks], [1, 3])
        self.assertEqual(
            [[page.number for page in chunk.pages] for chunk in chunks],
            [list(range(5, 9)), list(range(9, 13))],
        )

    def test_contiguous_outline_still_converts_every_chapter(self) -> None:
        self.outline["chapters"] = [
            {
                "number": 1,
                "title": "Chapter One",
                "printed_start_page": 1,
                "ocr_start_page": 5,
                "ocr_end_page": 8,
                "sections": [],
            },
            {
                "number": 2,
                "title": "Chapter Two",
                "printed_start_page": 20,
                "ocr_start_page": 9,
                "ocr_end_page": 12,
                "sections": [],
            },
        ]
        ocr_to_tex_agent.validate_chapter_manifest(
            self.outline, self.page_manifest, self.pages
        )
        chunks = ocr_to_tex_agent.plan_chapter_chunks(
            self.outline, self.pages, max_chars=10_000
        )
        self.assertEqual([chunk.chapter_number for chunk in chunks], [1, 2])

    def test_only_one_null_bound_is_rejected(self) -> None:
        self.outline["chapters"][1]["ocr_start_page"] = 9
        with self.assertRaises(ocr_to_tex_agent.AgentPipelineError):
            ocr_to_tex_agent.validate_chapter_manifest(
                self.outline, self.page_manifest, self.pages
            )

    def test_unheaded_bibliography_page_is_a_back_matter_boundary(self) -> None:
        page = self.pages[0]
        page.path.write_text(
            "J. W. Alexander, On types of knotted curves (1927), Annals.\n"
            "A. Andreotti, The Lefschetz theorem (1959), Annals.\n",
            encoding="utf-8",
        )
        self.assertTrue(ocr_to_tex_agent.is_back_matter_boundary(page))

    def test_sanitizer_repairs_unbalanced_subset_expression(self) -> None:
        source = "Any normal subgroup N\\subsetG determines a covering."
        self.assertEqual(
            ocr_to_tex_agent.sanitize_english_tex(source),
            "Any normal subgroup $N\\subset G$ determines a covering.",
        )

    def test_sanitizer_removes_source_example_identifier(self) -> None:
        self.assertEqual(
            ocr_to_tex_agent.sanitize_english_tex(r"\begin{example}[9.7]"),
            r"\begin{example}",
        )

    def test_sanitizer_repairs_recurring_ocr_word_fragments(self) -> None:
        source = (
            "an isolated ero?; an alternative uscription; all fi-brations; "
            "the Steifel manifold; a real.valued function"
        )
        self.assertEqual(
            ocr_to_tex_agent.sanitize_english_tex(source),
            "an isolated zero; an alternative description; all fibrations; "
            "the Stiefel manifold; a real-valued function",
        )

    def test_sanitizer_repairs_verified_formula_ocr(self) -> None:
        source = (
            r"\zeta(t)=\exp\sum_{j=1}^{\infty}x_j t^j/j; "
            r"if $m$ is even; where $x_j=\Sigma_{d|j}d r_d$"
        )
        self.assertEqual(
            ocr_to_tex_agent.sanitize_english_tex(source),
            r"\zeta(t)=\exp\sum_{j=1}^{\infty}\chi_j t^j/j; "
            r"if $m$ is even; where $\chi_j=\sum_{d\mid j}d r_d$",
        )


if __name__ == "__main__":
    unittest.main()
