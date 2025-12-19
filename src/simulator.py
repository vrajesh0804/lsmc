from flask import Flask, request, Response
import threading
from typing import List
from src.execution_space import (
    generate_interleavings,
    split_by_thread,
    dump_all_executions,
)
import requests

LOCALSTACK_URL = "http://localhost:9999"
AWS_METHODS = ["GET", "POST", "PUT", "DELETE"]

app = Flask(__name__)

lock = threading.Lock()
condition = threading.Condition(lock)

# -----------------------------
# Global state
# -----------------------------
recorded_flow: List[str] = []          # first execution
all_valid_flows: List[List[str]] = []  # all interleavings
current_flow: List[str] = []
executed_steps: List[str] = []

flow_locked = False
current_step = 0
run_index = 0
dumped = False

# -----------------------------
# Helpers
# -----------------------------


def step_key(client_id: str, thread_id: str, method: str, path: str) -> str:
    return f"{client_id}:{thread_id}:{method}:{path}"


def forward_request(method: str, path: str):
    url = f"{LOCALSTACK_URL}/{path}"
    headers = {k: v for k, v in request.headers if k.lower() != "host"}
    try:
        return requests.request(
            method=method,
            url=url,
            headers=headers,
            data=request.get_data(),
            params=request.args,
            timeout=10,
        )
    except requests.RequestException as e:
        return Response(str(e), status=500)


def wait_for_turn(step: str):
    global current_step
    while step != current_flow[current_step]:
        condition.wait()


def advance_step(step: str):
    global current_step
    executed_steps.append(step)
    current_step += 1
    condition.notify_all()


# -----------------------------
# Flask routes
# -----------------------------


@app.route("/__execution_done__", methods=["POST"])
def execution_done():
    global flow_locked, all_valid_flows, current_flow
    global current_step, executed_steps, run_index, dumped

    with condition:
        executed_steps = []
        current_step = 0

        # First run: compute execution space
        if not flow_locked:
            flow_locked = True
            thread_flows = split_by_thread(recorded_flow)
            all_valid_flows = list(generate_interleavings(thread_flows))

            if not dumped:
                dump_all_executions(all_valid_flows)
                dumped = True
                print(f"🧠 Total valid executions: {len(all_valid_flows)}")
                print("📄 Written to all_executions.txt")

            current_flow = all_valid_flows[0]

        else:
            run_index += 1
            if run_index >= len(all_valid_flows):
                print("✅ All valid executions explored.")
                return "DONE", 200

            current_flow = all_valid_flows[run_index]

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
        else:
            wait_for_turn(step)
            advance_step(step)

    response = forward_request(method, path)
    return Response(
        response.content,
        status=response.status_code,
        headers=dict(response.headers),
    )