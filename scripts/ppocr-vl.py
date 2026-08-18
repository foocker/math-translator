# Please make sure the requests library is installed
# pip install requests
import argparse
import json
import os
import requests
import sys
import time
from pathlib import Path

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PaddleOCR-VL-1.6"
PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"


def load_env_file(path: Path) -> None:
    """Load simple KEY=value entries without overriding real environment vars."""
    if not path.exists():
        return

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise SystemExit(f"Invalid entry in {path} at line {line_number}")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit a local file or URL to PaddleOCR-VL")
    parser.add_argument("input", help="local file path or file URL")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR / "output",
        help="output directory; created automatically (default: tools/math-translator/output)",
    )
    parser.add_argument("--ocr-only", action="store_true", help="stop after saving the original OCR output")
    return parser.parse_args()


args = parse_args()
load_env_file(ENV_FILE)
TOKEN = os.environ.get("PADDLEOCR_TOKEN")
if not TOKEN:
    raise SystemExit(
        f"PADDLEOCR_TOKEN is not set. Create {ENV_FILE} from .env.example "
        "or export it in the environment."
    )

# Keep the PaddleOCR request flow below unchanged. Input and output locations
# are resolved here so the command works from any current directory.
file_path = args.input
output_dir = args.output_dir.expanduser().resolve()

headers = {
    "Authorization": f"bearer {TOKEN}",
}

optional_payload = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}

print(f"Processing file: {file_path}")
if not file_path.startswith("http") and not os.path.exists(file_path):
    print(f"Error: File not found at {file_path}")
    sys.exit(1)
output_dir.mkdir(parents=True, exist_ok=True)
print(f"Output directory: {output_dir}")

if file_path.startswith("http"):
    # URL Mode
    headers["Content-Type"] = "application/json"
    payload = {
        "fileUrl": file_path,
        "model": MODEL,
        "optionalPayload": optional_payload
    }
    job_response = requests.post(JOB_URL, json=payload, headers=headers)
else:
    # Local File Mode
    data = {
        "model": MODEL,
        "optionalPayload": json.dumps(optional_payload)
    }
    
    with open(file_path, "rb") as f:
        files = {"file": f}
        job_response = requests.post(JOB_URL, headers=headers, data=data, files=files)

print(f"Response status: {job_response.status_code}")
if job_response.status_code != 200:
    print(f"Response content: {job_response.text}")

assert job_response.status_code == 200
jobId = job_response.json()["data"]["jobId"]
print(f"Job submitted successfully. job id: {jobId}")
print("Start polling for results")

jsonl_url = ""
while True:
    job_result_response = requests.get(f"{JOB_URL}/{jobId}", headers=headers)
    assert job_result_response.status_code == 200
    state = job_result_response.json()["data"]["state"]
    if state == 'pending':
        print("The current status of the job is pending")
    elif state == 'running':
        try:
            total_pages = job_result_response.json()['data']['extractProgress']['totalPages']
            extracted_pages = job_result_response.json()['data']['extractProgress']['extractedPages']
            print(f"The current status of the job is running, total pages: {total_pages}, extracted pages: {extracted_pages}")
        except KeyError:
             print("The current status of the job is running...")
    elif state == 'done':
        extracted_pages = job_result_response.json()['data']['extractProgress']['extractedPages']
        start_time = job_result_response.json()['data']['extractProgress']['startTime']
        end_time = job_result_response.json()['data']['extractProgress']['endTime']
        print(f"Job completed, successfully extracted pages: {extracted_pages}, start time: {start_time}, end time: {end_time}")
        jsonl_url = job_result_response.json()['data']['resultUrl']['jsonUrl']
        break
    elif state == "failed":
        error_msg = job_result_response.json()['data']['errorMsg']
        print(f"Job failed, failure reason：{error_msg}")
        sys.exit()

    time.sleep(5)

if jsonl_url:
    jsonl_response = requests.get(jsonl_url)
    jsonl_response.raise_for_status()
    lines = jsonl_response.text.strip().split('\n')
    page_num = 0
    for line_num, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        result = json.loads(line)["result"]
        for i, res in enumerate(result["layoutParsingResults"]):
            md_filename = os.path.join(output_dir, f"doc_{page_num}.md")
            with open(md_filename, "w", encoding="utf-8") as md_file:
                md_file.write(res["markdown"]["text"])
            print(f"Markdown document saved at {md_filename}")
            for img_path, img in res["markdown"]["images"].items():
                full_img_path = os.path.join(output_dir, img_path)
                os.makedirs(os.path.dirname(full_img_path), exist_ok=True)
                img_bytes = requests.get(img).content
                with open(full_img_path, "wb") as img_file:
                    img_file.write(img_bytes)
                print(f"Image saved to: {full_img_path}")
            for img_name, img in res["outputImages"].items():
                img_response = requests.get(img)
                if img_response.status_code == 200:
                    # Save image to local
                    filename = os.path.join(output_dir, f"{img_name}_{page_num}.jpg")
                    with open(filename, "wb") as f:
                        f.write(img_response.content)
                    print(f"Image saved to: {filename}")
                else:
                    print(f"Failed to download image, status code: {img_response.status_code}")
            page_num += 1

    # Agent processing is a separate module. Use --ocr-only to stop after the
    # untouched OCR output has been written.
    if not args.ocr_only:
        agent_script = os.path.join(os.path.dirname(__file__), "ocr_to_tex_agent.py")
        agent_args = [
            sys.executable,
            agent_script,
            "--output-dir",
            str(output_dir),
            "--phase",
            "all",
            "--page-count",
            str(page_num),
            "--overwrite",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        agent_exit_code = os.spawnv(os.P_WAIT, sys.executable, agent_args)
        if agent_exit_code != 0:
            print(f"TeX agent failed with exit code: {agent_exit_code}")
            sys.exit(agent_exit_code)
