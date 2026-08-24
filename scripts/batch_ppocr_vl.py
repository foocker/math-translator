"""Batch-submit numbered PNG pages to PaddleOCR-VL.

Each image is submitted as a separate API job. Markdown is saved as
``<page>.md`` and returned assets are kept under ``pages/<page>/``. The status
file makes interrupted runs resumable.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PaddleOCR-VL-1.6"
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ENV_FILE = PROJECT_DIR / ".env"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise SystemExit(f"Invalid entry in {path} at line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch PaddleOCR-VL parsing for numbered PNG pages")
    parser.add_argument("input_dir", type=Path, nargs="?", default=PROJECT_DIR / "dictionaries" / "pdf_full_pages_375dpi")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_DIR / "dictionaries" / "pdf_ocr_375dpi")
    parser.add_argument("--from-page", type=int, default=1)
    parser.add_argument("--to-page", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true", help="reprocess existing Markdown files")
    parser.add_argument("--submit-retries", type=int, default=30, help="retries when the service queue is full")
    parser.add_argument("--retry-seconds", type=float, default=15.0, help="delay between queue retries")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="delay between job status requests")
    parser.add_argument("--request-timeout", type=float, default=120.0)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_result(session: requests.Session, result: dict, page: int, page_dir: Path, output_md: Path, timeout: float) -> None:
    layouts = result.get("layoutParsingResults") or []
    if not layouts:
        raise RuntimeError("API result contains no layoutParsingResults")
    texts = [(item.get("markdown") or {}).get("text", "") for item in layouts]
    output_md.write_text("\n\n".join(texts), encoding="utf-8")
    for item_index, item in enumerate(layouts):
        markdown = item.get("markdown") or {}
        for relative, url in (markdown.get("images") or {}).items():
            target = page_dir / str(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            target.write_bytes(response.content)
        for image_name, url in (item.get("outputImages") or {}).items():
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            target = page_dir / f"{image_name}_{page}_{item_index}.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)


def submit_job(session: requests.Session, image_path: Path, headers: dict, args: argparse.Namespace) -> str:
    data = {"model": MODEL, "optionalPayload": json.dumps({
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    })}
    for attempt in range(1, args.submit_retries + 1):
        try:
            with image_path.open("rb") as image_file:
                response = session.post(JOB_URL, headers=headers, data=data, files={"file": image_file}, timeout=args.request_timeout)
            if response.status_code == 200:
                job_id = (response.json().get("data") or {}).get("jobId")
                if job_id:
                    return str(job_id)
                raise RuntimeError(f"submission response has no jobId: {response.text[:500]}")
            try:
                body = response.json()
            except ValueError:
                body = {}
            code = body.get("code") if isinstance(body, dict) else None
            message = (body.get("msg") if isinstance(body, dict) else None) or response.text[:500]
            if response.status_code == 400 and str(code) == "10010":
                print(f"  queue full; retry {attempt}/{args.submit_retries} in {args.retry_seconds:g}s", flush=True)
                time.sleep(args.retry_seconds)
                continue
            raise RuntimeError(f"HTTP {response.status_code}: {message}")
        except requests.RequestException as exc:
            if attempt == args.submit_retries:
                raise RuntimeError(f"request failed after {attempt} attempts: {exc}") from exc
            print(f"  request error; retry {attempt}/{args.submit_retries} in {args.retry_seconds:g}s", flush=True)
            time.sleep(args.retry_seconds)
    raise RuntimeError("submission retries exhausted")


def wait_for_result(session: requests.Session, job_id: str, headers: dict, args: argparse.Namespace) -> str:
    while True:
        response = session.get(f"{JOB_URL}/{job_id}", headers=headers, timeout=args.request_timeout)
        response.raise_for_status()
        data = response.json().get("data") or {}
        state = data.get("state")
        if state == "done":
            url = (data.get("resultUrl") or {}).get("jsonUrl")
            if not url:
                raise RuntimeError("completed job has no resultUrl.jsonUrl")
            return str(url)
        if state == "failed":
            raise RuntimeError(f"job failed: {data.get('errorMsg', 'unknown error')}")
        progress = data.get("extractProgress") or {}
        if state == "running" and progress:
            print(f"  job running: {progress.get('extractedPages', '?')}/{progress.get('totalPages', '?')}", flush=True)
        else:
            print(f"  job state: {state}", flush=True)
        time.sleep(args.poll_seconds)


def main() -> int:
    args = parse_args()
    load_env_file(ENV_FILE)
    token = os.environ.get("PADDLEOCR_TOKEN")
    if not token:
        raise SystemExit(f"PADDLEOCR_TOKEN is not set in {ENV_FILE}")
    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {input_dir}")
    if args.from_page < 1 or args.submit_retries < 1:
        raise SystemExit("page and retry values must be positive")
    numbers = sorted(int(p.stem) for p in input_dir.glob("*.png") if p.stem.isdecimal() and int(p.stem) > 0)
    last_page = args.to_page if args.to_page is not None else (numbers[-1] if numbers else 0)
    selected = [n for n in numbers if args.from_page <= n <= last_page]
    if not selected:
        raise SystemExit("No numbered PNG files in requested range")
    output_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"
    pages_dir.mkdir(exist_ok=True)
    status_path = output_dir / "status.json"
    status = {"input_dir": str(input_dir), "output_dir": str(output_dir), "from_page": args.from_page,
              "to_page": last_page, "total": len(selected), "completed": [], "failed": [], "updated_at": utc_now()}
    headers = {"Authorization": f"bearer {token}"}
    session = requests.Session()
    for position, page in enumerate(selected, 1):
        image_path = input_dir / f"{page}.png"
        output_md = output_dir / f"{page}.md"
        page_dir = pages_dir / str(page)
        if output_md.exists() and not args.overwrite:
            status["completed"].append(page)
            print(f"[{position}/{len(selected)}] skip {page}.png", flush=True)
            continue
        page_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{position}/{len(selected)}] parsing {image_path.name}", flush=True)
        try:
            job_id = submit_job(session, image_path, headers, args)
            print(f"  submitted job {job_id}", flush=True)
            result_url = wait_for_result(session, job_id, headers, args)
            response = session.get(result_url, timeout=args.request_timeout)
            response.raise_for_status()
            results = [json.loads(line).get("result") or {} for line in response.text.splitlines() if line.strip()]
            merged = {"layoutParsingResults": [item for result in results for item in result.get("layoutParsingResults", [])]}
            save_result(session, merged, page, page_dir, output_md, args.request_timeout)
            status["completed"].append(page)
            print(f"  saved {output_md.name}", flush=True)
        except Exception as exc:
            status["failed"].append({"page": page, "error": str(exc)})
            print(f"  FAILED page {page}: {exc}", flush=True)
        status["updated_at"] = utc_now()
        write_json(status_path, status)
    write_json(status_path, status)
    print(f"Done: {len(status['completed'])}/{len(selected)} completed; status: {status_path}")
    return 0 if not status["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
