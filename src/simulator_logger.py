# simulator_logger.py
import os
from datetime import datetime

# ---------------------------------------------------
# Correct logfile path (simulation root folder)
# This file lives inside: simulation/src/
# We want logfile in:    simulation/
# ---------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
LOG_FILE = os.path.join(BASE_DIR, "simulator_logs.txt")

# Initialize file once
if not os.path.isfile(LOG_FILE):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(
            f"━ Simulator Logs Started on "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ━\n\n"
        )

_request_count = 0


# ---------------------------------------------------
# SERVICE DETECTOR
# ---------------------------------------------------
def detect_service(headers, method, path):
    """Detects service based on headers + path conventions."""
    if headers.get("x-amz-target"):
        return "DynamoDB"

    if method in ("PUT", "GET", "DELETE") and "/" in path:
        return "S3"

    return "Unknown"


# ---------------------------------------------------
# DYNAMODB COMMAND DETECTOR
# ---------------------------------------------------
def detect_dynamodb_command(headers, body):
    """Extracts actual DynamoDB operation name."""
    target = headers.get("x-amz-target")
    if not target:
        return "Unknown DynamoDB Command"

    if "CreateTable" in target:
        return "CreateTable"
    if "ListTables" in target:
        return "ListTables"
    if "PutItem" in target:
        return "PutItem"

    return "Other DynamoDB Command"


# ---------------------------------------------------
# S3 COMMAND DETECTOR
# ---------------------------------------------------
def detect_s3_command(method, path, body):
    """Determine S3 operation based on HTTP method + path."""
    clean_path = path.strip("/")
    parts = clean_path.split("/") if clean_path else []

    # Create Bucket: PUT /bucketname  with empty body
    if method == "PUT" and len(parts) == 1 and not body.strip():
        return f"CreateBucket `{parts[0]}`"

    # List Buckets: GET /
    if method == "GET" and path == "/":
        return "ListBuckets"

    # Upload object: PUT /bucket/key
    if method == "PUT" and len(parts) > 1:
        return f"UploadObject `{clean_path}`"

    return "Other S3 Command"


# ---------------------------------------------------
# LOG FORMATTER
# ---------------------------------------------------
def log_request(client_ip, service, method, path, body, status, resp_headers, client_message):
    """Write clean, structured logs to file."""
    global _request_count
    _request_count += 1

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Clean body
    clean_body = body.strip() if body and body.strip() else "(empty)"

    # Extract important headers nicely
    important_headers = {
        key: resp_headers[key]
        for key in ["Location", "x-amz-request-id", "x-localstack"]
        if key in resp_headers
    }

    if not important_headers:
        important_headers = "(none)"

    # Build log entry
    log_entry = (
        f"{'━' * 60}\n"
        f"📝 Request #{_request_count}  —  {timestamp}\n"
        f"• Client IP: {client_ip}\n"
        f"• Service: {service}\n"
        f"• Method: {method}\n"
        f"• Path: {path}\n"
        f"• Body: {clean_body}\n"
        f"• LocalStack Status: {status}\n"
        f"• Important Headers: {important_headers}\n"
        f"• Client sees: {client_message}\n"
        f"{'━' * 60}\n\n"
    )

    # Append to file
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)
