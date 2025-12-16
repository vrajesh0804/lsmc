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

# -------------------------------
# GLOBAL STATE
# -------------------------------
request_memory_lock = threading.Lock()
request_memory = []

# Maps unique signature -> inferred thread id
thread_map = {}
thread_counter = 0

LOCALSTACK_URL = "http://localhost:9999"
AWS_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


# ----------------------------------------------------
def get_clean_body():
    raw_body = request.get_data(as_text=True)
    return raw_body if raw_body else "(empty)"


# ----------------------------------------------------
def identify_command(service, body, method, path, body_raw):
    if service == "DynamoDB":
        return detect_dynamodb_command(request.headers, body)

    if service == "S3":
        return detect_s3_command(method, path, body_raw)

    return "Unknown"


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
        return command

    return "Unknown command"


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
def infer_thread_id(client_ip, client_port, client_id):
    """
    Simulator guesses thread identity.
    """
    global thread_counter

    signature = f"{client_ip}:{client_port}:{client_id or 'NOID'}"

    with request_memory_lock:
        if signature not in thread_map:
            thread_counter += 1
            thread_map[signature] = thread_counter

        return signature, thread_map[signature]


# ----------------------------------------------------
# Thread inference memory
thread_fingerprints = {}
thread_counter = 0
thread_lock = threading.Lock()

@app.route("/", defaults={"path": ""}, methods=AWS_METHODS)
@app.route("/<path:path>", methods=AWS_METHODS)
def proxy(path):
    global thread_counter

    client_ip = request.remote_addr
    client_port = request.headers.get("X-Thread-Port") or request.environ.get("REMOTE_PORT")
    client_id = request.headers.get("X-Client-Id", "N/A")

    method = request.method
    path_full = f"/{path}"

    # ---- Thread inference logic ----
    fingerprint = f"{client_ip}|{client_port}|{client_id}"

    with thread_lock:
        if fingerprint not in thread_fingerprints:
            thread_counter += 1
            thread_fingerprints[fingerprint] = f"T{thread_counter}"

        thread_id = thread_fingerprints[fingerprint]

    # ---- Print inference nicely ----
    print(f"""
    🧠 Simulator Inference
       Client IP   : {client_ip}
       Client Port : {client_port}
       Client ID   : {client_id}
       Thread ID   : {thread_id}
       Request     : {method} {path_full}
    """)

    # ---- Forward request to LocalStack ----
    try:
        ls_response = forward_to_localstack(method, path)
    except Exception as e:
        return Response(f"Simulator Error: {str(e)}", status=500)

    return Response(
        ls_response.content,
        status=ls_response.status_code,
        headers=dict(ls_response.headers),
    )
