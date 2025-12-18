from flask import Flask, request, Response
import threading
import requests
from typing import List

LOCALSTACK_URL = "http://localhost:9999"
AWS_METHODS = ["GET", "POST", "PUT", "DELETE"]

app = Flask(__name__)

lock = threading.Lock()
condition = threading.Condition(lock)

recorded_flow: List[str] = []
flow_locked = False
current_step = 0


def step_key(client_id: str, thread_id: str, method: str, path: str) -> str:
    return f"{client_id}:{thread_id}:{method}:{path}"


def forward_request(method: str, path: str):
    url = f"{LOCALSTACK_URL}/{path}"
    headers = {k: v for k, v in request.headers if k.lower() != "host"}

    return requests.request(
        method=method,
        url=url,
        headers=headers,
        data=request.get_data(),
        params=request.args,
        timeout=10,
    )


def print_reference_flow():
    print("\n🔒 Execution flow locked")
    print("📋 Reference execution order:\n")
    for i, step in enumerate(recorded_flow, 1):
        client, thread, method, path = step.split(":", 3)
        print(f"  {i}. [{client}] {thread} → {method} {path}")
    print("\n🧠 Mode: ENFORCE (deterministic replay)\n")


def wait_for_turn(step: str):
    global current_step

    expected = recorded_flow[current_step]

    if step != expected:
        print(f"⏸️  PAUSE   {step}")
        print(f"   ↳ waiting for: {expected}")

    while step != recorded_flow[current_step]:
        condition.wait()


def advance_step():
    global current_step

    current_step += 1

    if current_step == len(recorded_flow):
        print("🔁 Execution cycle completed successfully\n")
        current_step = 0

    condition.notify_all()


@app.route("/__execution_done__", methods=["POST"])
def execution_done():
    global flow_locked
    with condition:
        if not flow_locked:
            flow_locked = True
            print_reference_flow()
        condition.notify_all()
    return "OK", 200


@app.route("/", defaults={"path": ""}, methods=AWS_METHODS)
@app.route("/<path:path>", methods=AWS_METHODS)
def proxy(path: str):
    global flow_locked

    client_id = request.headers.get("X-Client-Id", "unknown-client")
    thread_id = request.headers.get("X-Thread-Id", "unknown-thread")

    method = request.method
    path_full = f"/{path}"

    step = step_key(client_id, thread_id, method, path_full)

    with condition:
        if not flow_locked:
            recorded_flow.append(step)
            print(f"🧠 [RECORD] [{client_id}] {thread_id} → {method} {path_full}")
        else:
            wait_for_turn(step)
            print(f"▶️  RESUME  [{client_id}] {thread_id} → {method} {path_full}")
            advance_step()

    response = forward_request(method, path)

    return Response(
        response.content,
        status=response.status_code,
        headers=dict(response.headers),
    )
