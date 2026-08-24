"""Submit one local image to MinerU and print the API responses."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from zipfile import ZipFile

import requests


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"
UPLOAD_URL = "https://mineru.net/api/v4/file-urls/batch"
RESULT_URL = "https://mineru.net/api/v4/extract-results/batch/{batch_id}"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload one image or a numbered image directory to MinerU")
    parser.add_argument("image_or_dir", type=Path, nargs="?", default=PROJECT_DIR / "dictionaries" / "pdf_full_pages_375dpi")
    parser.add_argument("--model-version", default="vlm")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--no-poll", action="store_true", help="only upload and print submission response")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_DIR / "dictionaries" / "pdf_mineru_test")
    parser.add_argument("--result-url", help="download an existing full_zip_url without submitting again")
    parser.add_argument("--overwrite", action="store_true", help="reprocess Markdown files that already exist")
    parser.add_argument("--from-page", type=int, default=1, help="first numbered image in directory")
    parser.add_argument("--to-page", type=int, default=None, help="last numbered image in directory")
    parser.add_argument("--retry-seconds", type=float, default=30.0, help="wait after a full MinerU queue response")
    parser.add_argument("--submit-retries", type=int, default=10)
    parser.add_argument("--verbose", action="store_true", help="print every API response")
    return parser.parse_args()


def show(label: str, value: object) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_full_markdown(zip_path: Path, output_dir: Path, stem: str) -> Path:
    """Copy only the archive member named ``full.md`` to ``<stem>.md``."""
    destination = output_dir / f"{stem}.md"
    with ZipFile(zip_path) as archive:
        matches = [member for member in archive.infolist() if Path(member.filename).name.lower() == "full.md"]
        if not matches:
            raise RuntimeError("MinerU ZIP does not contain full.md")
        with archive.open(matches[0], "r") as source, destination.open("wb") as target:
            target.write(source.read())
    return destination


def download_and_extract(session: requests.Session, url: str, output_dir: Path, stem: str, timeout: float) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f".mineru-{stem}.zip"
    print(f"Downloading result ZIP: {url}")
    try:
        with session.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            with zip_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        markdown = extract_full_markdown(zip_path, output_dir, stem)
        print(f"Saved Markdown: {markdown}")
    finally:
        zip_path.unlink(missing_ok=True)


def process_image(session: requests.Session, image: Path, output_dir: Path, auth: dict, args: argparse.Namespace) -> None:
    body = {"files": [{"name": image.name, "is_ocr": True, "data_id": image.stem}], "model_version": args.model_version}
    for attempt in range(1, args.submit_retries + 1):
        response = session.post(
            UPLOAD_URL,
            headers={**auth, "Content-Type": "application/json"},
            json=body,
            timeout=args.timeout,
        )
        if args.verbose:
            print(f"Submission HTTP status: {response.status_code}")
        try:
            submission = response.json()
        except ValueError:
            raise RuntimeError(f"MinerU returned non-JSON submission: {response.text[:500]}")
        if args.verbose:
            show("submission response", submission)
        if response.status_code == 400 and str(submission.get("code")) == "10010":
            if attempt == args.submit_retries:
                raise RuntimeError("MinerU submission queue remained full")
            print(f"  queue full; retry {attempt}/{args.submit_retries} in {args.retry_seconds:g}s", flush=True)
            time.sleep(args.retry_seconds)
            continue
        response.raise_for_status()
        data = submission.get("data") or {}
        batch_id = data.get("batch_id") or data.get("batchId")
        urls = data.get("file_urls") or data.get("fileUrls") or []
        if not batch_id or not urls:
            raise RuntimeError("MinerU submission response has no batch_id/file_urls")
        with image.open("rb") as file_handle:
            upload_response = session.put(urls[0], data=file_handle, timeout=args.timeout)
        if args.verbose:
            print(f"File upload HTTP status: {upload_response.status_code}")
        upload_response.raise_for_status()
        if args.no_poll:
            return
        endpoint = RESULT_URL.format(batch_id=batch_id)
        while True:
            result_response = session.get(endpoint, headers=auth, timeout=args.timeout)
            result_response.raise_for_status()
            result = result_response.json()
            if args.verbose:
                show("result response", result)
            items = (result.get("data") or {}).get("extract_result") or []
            if items:
                item = items[0]
                state = item.get("state") or item.get("status")
                if state in {"done", "success"}:
                    zip_url = item.get("full_zip_url") or item.get("fullZipUrl")
                    if not zip_url:
                        raise RuntimeError("MinerU completed without full_zip_url")
                    download_and_extract(session, zip_url, output_dir, image.stem, args.timeout)
                    return
                if state in {"failed", "error"}:
                    raise RuntimeError(f"MinerU failed: {item.get('err_msg', '')}")
                print(f"  {image.name}: {state}", flush=True)
            time.sleep(args.poll_seconds)
    raise RuntimeError("MinerU submission retries exhausted")


def main() -> int:
    args = parse_args()
    load_env(ENV_FILE)
    token = os.environ.get("MINERU_TOKEN")
    if not token:
        raise SystemExit(f"MINERU_TOKEN is not set in {ENV_FILE}")
    source = args.image_or_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    auth = {"Authorization": f"Bearer {token}"}

    if source.is_file():
        if args.result_url:
            with requests.Session() as session:
                download_and_extract(session, args.result_url, output_dir, source.stem, args.timeout)
            return 0
        images = [source]
    elif source.is_dir():
        images = sorted(
            (p for p in source.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
             and p.stem.isdecimal()),
            key=lambda p: int(p.stem),
        )
        last = args.to_page if args.to_page is not None else (int(images[-1].stem) if images else 0)
        images = [p for p in images if args.from_page <= int(p.stem) <= last]
    else:
        raise SystemExit(f"Image or directory not found: {source}")
    if not images:
        raise SystemExit("No numbered images found in requested range")

    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "status.json"
    status = {"input": str(source), "output_dir": str(output_dir), "total": len(images), "completed": [], "failed": []}
    with requests.Session() as session:
        for position, image in enumerate(images, 1):
            markdown = output_dir / f"{image.stem}.md"
            if markdown.exists() and not args.overwrite:
                status["completed"].append(image.stem)
                print(f"[{position}/{len(images)}] skip {image.name}", flush=True)
                continue
            print(f"[{position}/{len(images)}] parsing {image.name}", flush=True)
            try:
                process_image(session, image, output_dir, auth, args)
                status["completed"].append(image.stem)
            except Exception as exc:
                status["failed"].append({"page": image.stem, "error": str(exc)})
                print(f"  FAILED {image.name}: {exc}", flush=True)
            status["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            write_json(status_path, status)
    write_json(status_path, status)
    print(f"Done: {len(status['completed'])}/{len(images)} completed; status: {status_path}")
    return 0 if not status["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
