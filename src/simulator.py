from flask import Flask, request, Response
import os
import threading
import time
import logging
from collections import deque
from typing import List, Tuple, Set

from src.simcore.http_proxy import forward_to_localstack
from src.simcore.sim_log import write_sim_log
from src.simcore.scheduler import PrefixScheduler, RUN_SUCCESS, RUN_TIMEOUT
from src.simcore.dpor import parse_step, generate_forced_prefixes, DROP_PREFIX, undrop
from src.simcore.sim_helpers import (
    step_key,
    pretty_step,
    print_prefix,
    classify_http_failure,
    reset_localstack_quiet,
)

logging.getLogger("werkzeug").setLevel(logging.ERROR)

LOCALSTACK_URL = "http://localhost:9999"
AWS_METHODS = ["GET", "POST", "PUT", "DELETE", "HEAD"]
FORCED_PREFIX_DEADLOCK_TIMEOUT = int(os.environ.get("SIM_FORCED_PREFIX_TIMEOUT", "15"))

RUN_FAILURE = "FAILURE"
RUN_CRASH = "CRASH"

TREAT_404_AS_FAIL = os.environ.get("SIM_404_AS_FAIL", "0") == "1"
print(f"[SIM] 404 treated as failure: {TREAT_404_AS_FAIL}")

app = Flask(__name__)

lock = threading.Lock()
cond = threading.Condition(lock)

done = False
run_index = 0

current_result = {"value": RUN_SUCCESS}
failure_reason = {"value": ""}

trace: List[str] = []
forced_prefix: List[str] = []

pending_prefixes = deque()
seen_prefixes: Set[Tuple[str, ...]] = set()
explored_prefixes: Set[Tuple[str, ...]] = set()

# traces kept only for stats (NOT used to skip expansion)
seen_traces: Set[Tuple[str, ...]] = set()

results: List[Tuple[str, str]] = []

scheduler = PrefixScheduler(
    cond=cond,
    deadlock_timeout_s=FORCED_PREFIX_DEADLOCK_TIMEOUT,
    current_result_ref=current_result,
    failure_reason_ref=failure_reason,
)


def _print_final_summary(results_list: List[Tuple[str, str]]) -> None:
    success = sum(1 for r, _ in results_list if r == RUN_SUCCESS)
    failure = sum(1 for r, _ in results_list if r == RUN_FAILURE)
    crash = sum(1 for r, _ in results_list if r == RUN_CRASH)
    timeout = sum(1 for r, _ in results_list if r == RUN_TIMEOUT)

    print("\n📊 FINAL SUMMARY", flush=True)
    print(f"  ✅ Success     : {success}", flush=True)
    print(f"  ❌ Failure     : {failure}", flush=True)
    print(f"  💥 Crash       : {crash}", flush=True)
    print(f"  ⏱ Timeout     : {timeout}", flush=True)


def pick_next_prefix_or_done():
    global done, forced_prefix
    if pending_prefixes:
        forced_prefix = pending_prefixes.popleft()
        scheduler.start_run(forced_prefix)
        print_prefix(forced_prefix, parse_step)
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


@app.route("/__ready__", methods=["GET"])
def ready():
    return ("DONE", 200) if done else ("READY", 200)


@app.route("/__execution_done__", methods=["POST"])
def execution_done():
    global run_index, trace, done

    payload = request.get_json(silent=True) or {}
    is_crash = bool(payload.get("crash"))
    exit_code = payload.get("exit_code")

    with cond:
        run_index += 1

        if is_crash and current_result["value"] == RUN_SUCCESS:
            current_result["value"] = RUN_CRASH
            failure_reason["value"] = f"Client crashed (exit code {exit_code})"

        results.append((current_result["value"], failure_reason["value"]))

        if current_result["value"] == RUN_SUCCESS:
            print(f"✅ Execution {run_index}: SUCCESS", flush=True)
        elif current_result["value"] == RUN_FAILURE:
            print(f"❌ Execution {run_index}: FAILURE — {failure_reason['value']}", flush=True)
        elif current_result["value"] == RUN_CRASH:
            print(f"💥 Execution {run_index}: CRASH — {failure_reason['value']}", flush=True)
        else:
            # RUN_TIMEOUT
            print(f"⏱ Execution {run_index}: TIMEOUT — {failure_reason['value']}", flush=True)

        explored_prefixes.add(tuple(forced_prefix))

        # Always expand unless the run timed out (timeouts are incomplete traces)
        new_prefixes: List[List[str]] = []
        if current_result["value"] != RUN_TIMEOUT:
            seen_traces.add(tuple(trace))
            new_prefixes = generate_forced_prefixes(trace, seen_prefixes, explored_prefixes)

        # Deterministic order of enqueue
        new_prefixes_sorted = sorted(new_prefixes, key=lambda p: tuple(p))

        for p in new_prefixes_sorted:
            tp = tuple(p)
            if tp not in explored_prefixes:
                pending_prefixes.append(p)

        print(f"\n🧠 Discovered {len(new_prefixes_sorted)} new prefixes", flush=True)
        print(f"📦 Pending prefixes: {len(pending_prefixes)}", flush=True)

        # Reset for next execution
        trace = []
        scheduler.start_run([])  # free run until we set next forced prefix
        current_result["value"] = RUN_SUCCESS
        failure_reason["value"] = ""

        reset_localstack_quiet()
        pick_next_prefix_or_done()

        if done:
            _print_final_summary(results)
            print("✅ All executions explored.", flush=True)

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

    base_step = step_key(client, thread, method, full_path)

    with cond:
        expected_now = None
        if scheduler.enforcing and not scheduler.is_prefix_complete():
            expected_now = scheduler.forced_prefix[scheduler.forced_pos]

        presented = base_step
        drop_now = False

        # If scheduler expects a DROP::<step> and request matches, treat it as DROP step
        if expected_now and expected_now.startswith(DROP_PREFIX) and undrop(expected_now) == base_step:
            presented = expected_now
            drop_now = True

        ok = scheduler.wait_for_turn(presented)
        if not ok:
            write_sim_log(
                parse_step,
                base_step,
                408,
                "Blocked by forced prefix / run ended",
                run_index + 1,
                len(trace) + 1,
            )
            return Response("Timeout", status=408)

        scheduler.maybe_stop_enforcing()

        trace.append(presented)
        step_index = len(trace)

        if expected_now:
            print(
                f"[SCHED] run={run_index+1} ENFORCING "
                f"expected={pretty_step(expected_now, parse_step)} "
                f"got={pretty_step(presented, parse_step)}",
                flush=True,
            )
        else:
            print(
                f"[SCHED] run={run_index+1} FREE got={pretty_step(presented, parse_step)}",
                flush=True,
            )

        # DROP: do not forward, do not set run failure, just return 408
        if drop_now:
            print(
                f"[DROP] run={run_index+1} dropped={pretty_step(presented, parse_step)}",
                flush=True,
            )
            write_sim_log(
                parse_step,
                base_step,
                408,
                "Dropped by simulator",
                run_index + 1,
                step_index,
            )
            return Response("Dropped", status=408)

    # Forward to LocalStack outside lock
    try:
        resp = forward_to_localstack(LOCALSTACK_URL, method, path, timeout=10)
        status = resp.status_code

        if current_result["value"] == RUN_SUCCESS:
            reason = classify_http_failure(
                status=status,
                method=method,
                full_path=full_path,
                treat_404_as_fail=TREAT_404_AS_FAIL,
            )
            if reason is not None:
                current_result["value"] = RUN_FAILURE
                failure_reason["value"] = reason

        write_sim_log(
            parse_step,
            base_step,
            status,
            f"HTTP {status}",
            run_index + 1,
            step_index,
        )
        return Response(resp.content, status, resp.headers)

    except Exception:
        if current_result["value"] == RUN_SUCCESS:
            current_result["value"] = RUN_TIMEOUT
            failure_reason["value"] = "Exception while forwarding request"

        write_sim_log(
            parse_step,
            base_step,
            408,
            "Timeout (exception)",
            run_index + 1,
            len(trace),
        )
        return Response("Timeout", status=408)


# Initial seed: explore empty prefix first
with cond:
    seen_prefixes.add(tuple([]))
    pending_prefixes.append([])
    pick_next_prefix_or_done()
