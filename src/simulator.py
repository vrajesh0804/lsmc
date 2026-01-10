from flask import Flask, request, Response
import threading
import time
import logging
from collections import deque
from typing import List, Tuple

from src.simcore.http_proxy import forward_to_localstack
from src.simcore.sim_log import write_sim_log
from src.simcore.scheduler import PrefixScheduler, RUN_SUCCESS, RUN_TIMEOUT
from src.simcore.dpor import parse_step, generate_forced_prefixes

# ---------------- CONFIG ----------------

logging.getLogger("werkzeug").setLevel(logging.ERROR)

LOCALSTACK_URL = "http://localhost:9999"
AWS_METHODS = ["GET", "POST", "PUT", "DELETE", "HEAD"]

FORCED_PREFIX_DEADLOCK_TIMEOUT = 8

RUN_FAILURE = "FAILURE"
RUN_CRASH = "CRASH"

# ---------------- APP + GLOBAL STATE ----------------

app = Flask(__name__)

lock = threading.Lock()
cond = threading.Condition(lock)

done = False
run_index = 0

# We store these in small dicts so scheduler can mutate them without circular imports
current_result = {"value": RUN_SUCCESS}
failure_reason = {"value": ""}

trace: List[str] = []          # full trace recorded per run
forced_prefix: List[str] = []  # chosen prefix for current run (for printing/dedup)

pending_prefixes = deque()     # queue[prefix]
seen_prefixes = set()          # set(tuple(prefix))
explored_prefixes = set()      # set(tuple(prefix))

results: List[Tuple[str, str]] = []  # (result, reason)

scheduler = PrefixScheduler(
    cond=cond,
    deadlock_timeout_s=FORCED_PREFIX_DEADLOCK_TIMEOUT,
    current_result_ref=current_result,
    failure_reason_ref=failure_reason,
)

# ---------------- Helpers ----------------

def step_key(client: str, thread: str, method: str, path: str) -> str:
    return f"{client}:{thread}:{method}:{path}"


def print_prefix(prefix: List[str]):
    if not prefix:
        print("\n📌 Forced prefix: (empty) — free run / discovery")
        return
    print(f"\n📌 Forced prefix (length={len(prefix)})")
    for i, s in enumerate(prefix, start=1):
        print(f"  {i:02d}. {s}")


def pick_next_prefix_or_done():
    global done, forced_prefix
    if pending_prefixes:
        forced_prefix = pending_prefixes.popleft()
        scheduler.start_run(forced_prefix)
        print_prefix(forced_prefix)
    else:
        done = True


def watchdog_loop():
    while True:
        time.sleep(1)
        with cond:
            if done:
                return
            scheduler.watchdog_tick()


threading.Thread(target=watchdog_loop, daemon=True).start()

# ---------------- Routes ----------------

@app.route("/__ready__", methods=["GET"])
def ready():
    return ("DONE", 200) if done else ("READY", 200)


@app.route("/__execution_done__", methods=["POST"])
def execution_done():
    global run_index, trace
    payload = request.get_json(silent=True) or {}
    is_crash = bool(payload.get("crash"))
    exit_code = payload.get("exit_code")

    with cond:
        run_index += 1

        # Crash overrides
        if is_crash and current_result["value"] == RUN_SUCCESS:
            current_result["value"] = RUN_CRASH
            failure_reason["value"] = f"Client crashed (exit code {exit_code})"

        results.append((current_result["value"], failure_reason["value"]))

        # Print outcome
        if current_result["value"] == RUN_SUCCESS:
            print(f"✅ Execution {run_index}: SUCCESS")
        elif current_result["value"] == RUN_FAILURE:
            print(f"❌ Execution {run_index}: FAILURE — {failure_reason['value']}")
        elif current_result["value"] == RUN_CRASH:
            print(f"💥 Execution {run_index}: CRASH — {failure_reason['value']}")
        else:
            print(f"⏱ Execution {run_index}: TIMEOUT — {failure_reason['value']}")

        explored_prefixes.add(tuple(forced_prefix))

        # DPOR expansion (trace -> prefixes)
        new_prefixes = generate_forced_prefixes(trace, seen_prefixes, explored_prefixes)
        for p in new_prefixes:
            pending_prefixes.append(p)

        print(f"\n🧠 Discovered {len(new_prefixes)} new forced prefixes from this run")
        print(f"📦 Pending prefixes: {len(pending_prefixes)}")

        # Reset run state
        trace = []
        scheduler.start_run([])                 # stop enforcing
        current_result["value"] = RUN_SUCCESS
        failure_reason["value"] = ""

        # Next prefix
        pick_next_prefix_or_done()

        if done:
            print("\n📊 FINAL SUMMARY")
            print(f"  ✅ Success : {sum(1 for r, _ in results if r == RUN_SUCCESS)}")
            print(f"  ❌ Failure : {sum(1 for r, _ in results if r == RUN_FAILURE)}")
            print(f"  💥 Crash   : {sum(1 for r, _ in results if r == RUN_CRASH)}")
            print(f"  ⏱ Timeout : {sum(1 for r, _ in results if r == RUN_TIMEOUT)}")

        cond.notify_all()

    return ("OK", 200)


@app.route("/", defaults={"path": ""}, methods=AWS_METHODS)
@app.route("/<path:path>", methods=AWS_METHODS)
def proxy(path):
    global trace

    client = request.headers.get("X-Client-Id", "unknown")
    thread = request.headers.get("X-Thread-Id", "unknown")
    method = request.method
    full_path = f"/{path}"

    step = step_key(client, thread, method, full_path)

    with cond:
        # Enforce forced prefix while active
        ok = scheduler.wait_for_turn(step)
        if not ok:
            write_sim_log(parse_step, step, 408, "Blocked by infeasible forced prefix", run_index + 1, len(trace) + 1)
            return Response("Timeout", status=408)

        scheduler.maybe_stop_enforcing()

        # Record full trace (including suffix)
        trace.append(step)
        step_index = len(trace)

    # Forward to LocalStack
    try:
        resp = forward_to_localstack(LOCALSTACK_URL, method, path, timeout=10)

        if resp.status_code >= 400 and current_result["value"] == RUN_SUCCESS:
            current_result["value"] = RUN_FAILURE
            failure_reason["value"] = f"{method} {full_path} → {resp.status_code}"

        write_sim_log(parse_step, step, resp.status_code, f"HTTP {resp.status_code}", run_index + 1, step_index)
        return Response(resp.content, resp.status_code, resp.headers)

    except Exception:
        if current_result["value"] == RUN_SUCCESS:
            current_result["value"] = RUN_TIMEOUT
            failure_reason["value"] = "Exception while forwarding request"
        write_sim_log(parse_step, step, 408, "Timeout (exception)", run_index + 1, len(trace))
        return Response("Timeout", status=408)

# ---------------- INIT ----------------
seen_prefixes.add(tuple([]))
print_prefix([])
