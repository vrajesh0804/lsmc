# src/simulator_logger.py
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
LOG_FILE = os.path.join(BASE_DIR, "simulator_logs.txt")

# create file if missing
if not os.path.isfile(LOG_FILE):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("=== Simulator Logs ===\n\n")


def detect_service(headers_lower: dict, method: str, path: str) -> str:
    # You can customize this; keep it simple for now.
    # Many of your requests are S3-like.
    if "x-amz-target" in headers_lower:
        return "dynamodb"
    return "s3"


def log_request(
    client_ip: str,
    service: str,
    method: str,
    path: str,
    body: str,
    status: int,
    resp_headers: dict,
    client_message: str,
    run_no: int,
    step_index: int,
):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    clean_body = (body or "").strip()
    if len(clean_body) > 400:
        clean_body = clean_body[:400] + "…"

    entry = (
        f"[{ts}] Run={run_no} Step={step_index}\n"
        f"ClientIP: {client_ip}\n"
        f"Service:  {service}\n"
        f"Request:  {method} {path}\n"
        f"Body:     {clean_body}\n"
        f"Status:   {status}\n"
        f"Client:   {client_message}\n"
        f"{'-'*60}\n"
    )

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
