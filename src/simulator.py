from flask import Flask, request, Response
import os
import threading
import time
import logging
from collections import deque
from typing import List, Tuple, Set

from src.simcore.http_proxy import forward_to_localstack
from src.simcore.sim_log import write_sim_log
from src.simcore.scheduler import (
    PrefixScheduler,
    RUN_SUCCESS,
    RUN_FAILURE,
    RUN_CRASH,
    RUN_TIMEOUT,
)
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

FORCED_PREFIX_DEADLOCK_TIMEOUT = int(os.environ.get("SIM_FORCED_PREFIX_TIMEOUT", "20"))
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


def _pick_next_prefix_or_done() -> None:
    global done, forced_prefix
    if pending_prefixes:
        forced_prefix = pending_prefixes.popleft()
        scheduler.start_run(forced_prefix)
        print_prefix(forced_prefix, parse_step)
    else:
        done = True


def _watchdog_loop():
    while True:
        time.sleep(0.25)
        with cond:
            if done:
                return
            scheduler.watchdog_tick()


threading.Thread(target=_watchdog_loop, daemon=True).start()


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

        status = current_result["value"]
        reason = failure_reason["value"]
        results.append((status, reason))

        if status == RUN_SUCCESS:
            print(f"✅ Execution {run_index}: SUCCESS", flush=True)
        elif status == RUN_FAILURE:
            print(f"❌ Execution {run_index}: FAILURE — {reason}", flush=True)
        elif status == RUN_CRASH:
            print(f"💥 Execution {run_index}: CRASH — {reason}", flush=True)
        else:
            print(f"⏱ Execution {run_index}: TIMEOUT — {reason}", flush=True)

        explored_prefixes.add(tuple(forced_prefix))

        new_prefixes: List[List[str]] = []
        if status in (RUN_SUCCESS, RUN_FAILURE):
            trace_tuple = tuple(trace)
            seen_traces.add(trace_tuple)

            all_new = generate_forced_prefixes(trace, seen_prefixes, explored_prefixes)

            filtered: List[List[str]] = []
            for p in all_new:
                tp = tuple(p)
                if tp in seen_traces and len(p) == len(trace):
                    continue
                filtered.append(p)

            # stable ordering
            new_prefixes = sorted(filtered, key=lambda x: (len(x), tuple(x)))

            for p in new_prefixes:
                tp = tuple(p)
                if tp not in explored_prefixes:
                    pending_prefixes.append(p)

        print(f"\n🧠 Discovered {len(new_prefixes)} new prefixes", flush=True)
        print(f"📦 Pending prefixes: {len(pending_prefixes)}", flush=True)

        trace = []
        current_result["value"] = RUN_SUCCESS
        failure_reason["value"] = ""

        scheduler.start_run([])
        reset_localstack_quiet()
        _pick_next_prefix_or_done()

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
        ok = scheduler.wait_for_turn(base_step)
        if not ok:
            write_sim_log(
                parse_step,
                base_step,
                408,
                "Blocked (run ended / not scheduled)",
                run_index + 1,
                len(trace) + 1,
            )
            return Response("Blocked", status=408)

        # What did we ACTUALLY match?
        matched, was_drop = scheduler.consume_last_match()
        # matched is either base_step or DROP::base_step (when expected was DROP)
        presented = matched if matched is not None else base_step

        scheduler.maybe_stop_enforcing()

        trace.append(presented)
        step_index = len(trace)

        # Logging: now it's correct and deterministic
        exp_info = "free"
        if scheduler.expected_now() is not None:
            # NOTE: expected_now() is now "next expected", not the one we just matched.
            # We log using "got=" only (and keep your free/expected style minimal).
            exp_info = "expected"

        if exp_info == "free":
            print(f"[SCHED] run={run_index+1} free={pretty_step(presented, parse_step)}", flush=True)
        else:
            print(f"[SCHED] run={run_index+1} got={pretty_step(presented, parse_step)}", flush=True)

        if was_drop:
            print(f"[DROP] run={run_index+1} {pretty_step(presented, parse_step)}", flush=True)
            write_sim_log(
                parse_step,
                base_step,
                408,
                "Dropped by simulator",
                run_index + 1,
                step_index,
            )
            cond.notify_all()
            return Response("Dropped", status=408)

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

        label = pretty_step(base_step, parse_step)
        outcome = "OK" if status < 400 else f"HTTP {status}"
        print(f"[STEP] run={run_index+1} step={step_index} {label} -> {outcome}", flush=True)

        return Response(resp.content, status, resp.headers)

    except Exception as e:
        if current_result["value"] == RUN_SUCCESS:
            current_result["value"] = RUN_TIMEOUT
            failure_reason["value"] = f"LocalStack/forwarding timeout or exception: {type(e).__name__}"

        write_sim_log(
            parse_step,
            base_step,
            408,
            "Timeout (forwarding exception)",
            run_index + 1,
            step_index,
        )

        label = pretty_step(base_step, parse_step)
        print(f"[STEP] run={run_index+1} step={step_index} {label} -> TIMEOUT/EXCEPTION", flush=True)

        return Response("Timeout", status=408)


with cond:
    seen_prefixes.add(tuple([]))
    pending_prefixes.append([])
    _pick_next_prefix_or_done()