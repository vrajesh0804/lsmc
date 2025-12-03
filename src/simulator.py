from flask import Flask, request, Response
import threading
import requests as py_requests
from src.simulator_logger import (
    log_request,
    detect_service,
    detect_dynamodb_command,
    detect_s3_command
)

app = Flask(__name__)

# Thread-safe request log
request_memory_lock = threading.Lock()
request_memory = []

LOCALSTACK_URL = "http://localhost:9999"
AWS_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


# ----------------------------------------------------
# Extract body safely
# ----------------------------------------------------
def get_clean_body():
    raw_body = request.get_data(as_text=True)
    return raw_body if raw_body else "(empty)"


# ----------------------------------------------------
# Determine the command (S3/DynamoDB/Other)
# ----------------------------------------------------
def identify_command(service, body, method, path, body_raw):
    if service == "DynamoDB":
        return detect_dynamodb_command(request.headers, body)

    if service == "S3":
        return detect_s3_command(method, path, body_raw)

    return "Unknown"


# ----------------------------------------------------
# Generate user-friendly client response message
# ----------------------------------------------------
def build_client_message(service, command, ls_response):
    if service == "DynamoDB":
        if "CreateTable" in command:
            return "DynamoDB table created" if ls_response.status_code == 200 else "Failed to create table"
        elif "ListTables" in command:
            return "Tables listed" if ls_response.status_code == 200 else "Failed to list tables"
        elif "PutItem" in command:
            return "Item inserted" if ls_response.status_code == 200 else "Failed to insert item"
        else:
            return "DynamoDB request processed"

    elif service == "S3":
        return command  # Already human-readable

    return "Unknown command"


# ----------------------------------------------------
# Forward request to LocalStack cleanly
# ----------------------------------------------------
def forward_to_localstack(method, path):
    url = f"{LOCALSTACK_URL}/{path}"

    filtered_headers = {
        k: v for k, v in request.headers if k.lower() != "host"
    }

    return py_requests.request(
        method=method,
        url=url,
        headers=filtered_headers,
        params=request.args,
        data=request.get_data(),
        timeout=10,
    )


# ----------------------------------------------------
# MAIN PROXY ENDPOINT
# ----------------------------------------------------
@app.route("/", defaults={"path": ""}, methods=AWS_METHODS)
@app.route("/<path:path>", methods=AWS_METHODS)
def proxy(path):
    client_ip = request.remote_addr

    # Prefer logical port sent by client thread, fallback to real REMOTE_PORT
    logical_port = request.headers.get("X-Thread-Port")
    if logical_port is None:
        logical_port = request.environ.get("REMOTE_PORT")  # Flask environ holds client port.[web:2]

    client_port = logical_port

    method = request.method
    path_full = f"/{path}"

    # >>> add this line to show port in simulator console
    print(f"[SIMULATOR] {client_ip}:{client_port} -> {method} {path_full}")

    body_raw = request.get_data()
    try:
        clean_body = body_raw.decode("utf-8") if body_raw else "(empty)"
    except UnicodeDecodeError:
        clean_body = "(binary data)"

    service = detect_service(request.headers, method, path_full)
    command = identify_command(service, clean_body, method, path_full, body_raw)

    try:
        ls_response = forward_to_localstack(method, path)
    except Exception as e:
        return Response(f"Simulator Error: {str(e)}", status=500)

    client_msg = build_client_message(service, command, ls_response)

    with request_memory_lock:
        request_memory.append({
            "order": len(request_memory) + 1,
            "ip": client_ip,
            "port": client_port,
            "service": service,
            "command": command,
            "method": method,
            "path": path_full,
            "body": clean_body,
            "status": ls_response.status_code
        })

    log_request(
        client_ip=client_ip,
        service=service,
        method=method,
        path=path_full,
        body=clean_body,
        status=ls_response.status_code,
        resp_headers=dict(ls_response.headers),
        client_message=client_msg,
    )

    headers = dict(ls_response.headers)
    headers["X-Client-IP"] = client_ip
    headers["X-Client-Port"] = str(client_port)

    return Response(
        ls_response.content,
        status=ls_response.status_code,
        headers=headers
    )
