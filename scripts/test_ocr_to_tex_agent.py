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


if __name__ == "__main__":
    unittest.main()
