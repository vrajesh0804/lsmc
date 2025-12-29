from flask import Flask, request, Response
import threading
import requests
from typing import List
from src.execution_space import split_by_thread, generate_interleavings

LOCALSTACK_URL = "http://localhost:9999"
AWS_METHODS = ["GET", "POST", "PUT", "DELETE"]

app = Flask(__name__)

lock = threading.Lock()
condition = threading.Condition(lock)

recorded_flow: List[str] = []
all_flows: List[List[str]] = []
current_flow: List[str] = []

flow_locked = False
current_step = 0
run_index = 0
done = False


def step_key(client, thread, method, path):
    return f"{client}:{thread}:{method}:{path}"


def forward_request(method, path):
    url = f"{LOCALSTACK_URL}/{path}"
    headers = {k: v for k, v in request.headers if k.lower() != "host"}
    try:
        return requests.request(
            method, url,
            headers=headers,
            data=request.get_data(),
            params=request.args,
            timeout=10
        )
    except requests.RequestException as e:
        return Response(str(e), status=500)


def wait_for_turn(step):
    global current_step
    while step != current_flow[current_step]:
        condition.wait()


def advance_step():
    global current_step
    current_step += 1
    condition.notify_all()


@app.route("/__execution_done__", methods=["POST"])
def execution_done():
    global flow_locked, all_flows, current_flow
    global run_index, current_step, done

    with condition:
        current_step = 0

        # First run → build execution space
        if not flow_locked:
            flow_locked = True
            thread_flows = split_by_thread(recorded_flow)
            all_flows = list(generate_interleavings(thread_flows))

            with open("all_executions.txt", "w", encoding="utf-8") as f:
                for i, flow in enumerate(all_flows, 1):
                    f.write(f"Execution {i}:\n")
                    for j, step in enumerate(flow, 1):
                        f.write(f"  {j}. {step}\n")
                    f.write("\n")

            current_flow = all_flows[0]
            run_index = 0
            print(f"🧠 Total valid executions: {len(all_flows)}")

        else:
            run_index += 1
            if run_index >= len(all_flows):
                done = True
                condition.notify_all()
                return "DONE", 200

            current_flow = all_flows[run_index]

        condition.notify_all()
    return "OK", 200


@app.route("/__ready__", methods=["GET"])
def ready():
    return ("DONE", 200) if done else ("READY", 200)


@app.route("/", defaults={"path": ""}, methods=AWS_METHODS)
@app.route("/<path:path>", methods=AWS_METHODS)
def proxy(path):
    client = request.headers.get("X-Client-Id", "unknown")
    thread = request.headers.get("X-Thread-Id", "unknown")
    method = request.method
    full_path = f"/{path}"

    step = step_key(client, thread, method, full_path)

    with condition:
        if not flow_locked:
            recorded_flow.append(step)
        else:
            wait_for_turn(step)
            advance_step()

    resp = forward_request(method, path)
    return Response(resp.content, resp.status_code, resp.headers)
