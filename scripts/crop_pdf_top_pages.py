"""Render and crop the top of consecutive PDF pages.

Page numbering is the PDF's physical 1-based page number. Output numbering is
natural-numbered from 1, so source page 18 becomes ``1.png`` by default.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description="截取 PDF 连续页面顶部并按自然数保存 PNG")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--from-page", type=int, default=18)
    parser.add_argument("--to-page", type=int, default=716)
    parser.add_argument("--height", type=int, default=320, help="裁剪高度，单位为渲染后的像素")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.from_page < 1 or args.to_page < args.from_page:
        raise SystemExit("页码范围无效")
    if args.height <= 0 or args.dpi <= 0:
        raise SystemExit("height 和 dpi 必须为正整数")
    if not args.pdf.is_file():
        raise SystemExit(f"找不到 PDF：{args.pdf}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    metadata = []
    with tempfile.TemporaryDirectory(prefix="pdf-top-render-") as tmp:
        tmp_prefix = Path(tmp) / "page"
        for output_index, source_page in enumerate(range(args.from_page, args.to_page + 1), start=1):
            output_path = args.output_dir / f"{output_index}.png"
            if output_path.exists() and not args.overwrite:
                raise SystemExit(f"文件已存在，使用 --overwrite 覆盖：{output_path}")

            prefix = str(tmp_prefix)
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(source_page),
                    "-l",
                    str(source_page),
                    "-r",
                    str(args.dpi),
                    "-png",
                    "-singlefile",
                    str(args.pdf),
                    prefix,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            rendered = Path(prefix + ".png")
            with Image.open(rendered) as image:
                crop_height = min(args.height, image.height)
                image.crop((0, 0, image.width, crop_height)).save(output_path, "PNG")
                metadata.append(
                    {
                        "output": output_path.name,
                        "source_page": source_page,
                        "width": image.width,
                        "height": crop_height,
                        "dpi": args.dpi,
                    }
                )
            rendered.unlink(missing_ok=True)
            if output_index == 1 or output_index % 50 == 0 or output_index == args.to_page - args.from_page + 1:
                print(f"{output_index}/{args.to_page - args.from_page + 1}: page {source_page}")

    (args.output_dir / "index.json").write_text(
        json.dumps(
            {
                "pdf": str(args.pdf),
                "from_page": args.from_page,
                "to_page": args.to_page,
                "height": args.height,
                "dpi": args.dpi,
                "pages": metadata,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
