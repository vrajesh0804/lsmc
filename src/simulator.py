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
from src.simcore.dpor import parse_step, generate_forced_prefixes
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
ENABLE_DROP = os.environ.get("SIM_ENABLE_DROP", "0") == "1"
print(f"[SIM] DROP enabled: {ENABLE_DROP}", flush=True)

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

# traces we have actually executed (unique)
seen_traces: Set[Tuple[str, ...]] = set()

# all prefixes of those traces, used to prevent scheduling duplicates BEFORE running them
explored_trace_prefixes: Set[Tuple[str, ...]] = set()

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
    """
    Pick the next forced prefix to execute.
    Hard requirement: do NOT run duplicates.
    That means: if forcing a prefix would deterministically replay an already-seen trace,
    we skip it BEFORE starting the run.
    """
    global done, forced_prefix

    while pending_prefixes:
        candidate = pending_prefixes.popleft()
        tp = tuple(candidate)

        # If we already tried this exact forced prefix, skip.
        if tp in explored_prefixes:
            continue

        # If this forced prefix is already a prefix of an explored trace,
        # the run is guaranteed (under deterministic completion) to be a duplicate.
        if tp in explored_trace_prefixes:
            continue

        forced_prefix = candidate
        scheduler.start_run(forced_prefix)

        # Print only what is actually forced (truthful)
        print_prefix(forced_prefix, parse_step)
        return

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
    """
    End-of-run handler.
    - Never count duplicates
    - Never expand DPOR from duplicates
    - Always reset + pick next
    """
    global run_index, trace, done, forced_prefix

    payload = request.get_json(silent=True) or {}
    is_crash = bool(payload.get("crash"))
    exit_code = payload.get("exit_code")

    with cond:
        run_index += 1

        # If client crashed but simulator thought success so far, mark crash.
        if is_crash and current_result["value"] == RUN_SUCCESS:
            current_result["value"] = RUN_CRASH
            failure_reason["value"] = f"Client crashed (exit code {exit_code})"

        status = current_result["value"]
        reason = failure_reason["value"]

        # Mark forced prefix as explored regardless.
        explored_prefixes.add(tuple(forced_prefix))

        trace_tuple = tuple(trace)

        # ----------------------------
        # HARD TRACE DEDUPE (silent)
        # ----------------------------
        if status in (RUN_SUCCESS, RUN_FAILURE) and trace_tuple in seen_traces:
            # Do not print anything, do not count anything, do not expand anything.
            trace = []
            current_result["value"] = RUN_SUCCESS
            failure_reason["value"] = ""

            scheduler.start_run([])
            reset_localstack_quiet()
            _pick_next_prefix_or_done()
            cond.notify_all()
            return ("OK", 200)

        # ----------------------------
        # Count unique runs
        # ----------------------------
        results.append((status, reason))

        if status == RUN_SUCCESS:
            print(f"✅ Execution {run_index}: SUCCESS", flush=True)
        elif status == RUN_FAILURE:
            print(f"❌ Execution {run_index}: FAILURE — {reason}", flush=True)
        elif status == RUN_CRASH:
            print(f"💥 Execution {run_index}: CRASH — {reason}", flush=True)
        else:
            print(f"⏱ Execution {run_index}: TIMEOUT — {reason}", flush=True)

        new_prefixes: List[List[str]] = []

        # Only expand DPOR from SUCCESS / FAILURE unique traces.
        if status in (RUN_SUCCESS, RUN_FAILURE):
            seen_traces.add(trace_tuple)

            # Add all prefixes of this trace so we never run deterministic duplicates
            for k in range(1, len(trace_tuple) + 1):
                explored_trace_prefixes.add(trace_tuple[:k])

            all_new = generate_forced_prefixes(
                trace,
                seen_prefixes,
                explored_prefixes,
                enable_drop=ENABLE_DROP,
            )

            # Filter:
            filtered: List[List[str]] = []
            for p in all_new:
                tp = tuple(p)

                if tp in explored_prefixes:
                    continue
                if tp in explored_trace_prefixes:
                    continue

                filtered.append(p)

            # Stable order
            new_prefixes = sorted(filtered, key=lambda x: (len(x), tuple(x)))

            for p in new_prefixes:
                pending_prefixes.append(p)

        trace = []
        current_result["value"] = RUN_SUCCESS
        failure_reason["value"] = ""

        scheduler.start_run([])
        reset_localstack_quiet()
        _pick_next_prefix_or_done()

        print(f"\n🧠 Discovered {len(new_prefixes)} new prefixes", flush=True)
        print(f"📦 Pending prefixes: {len(pending_prefixes)}", flush=True)

        if done:
            _print_final_summary(results)
            print("✅ All executions explored.", flush=True)

        cond.notify_all()

    return ("OK", 200)


@app.route("/", defaults={"path": ""}, methods=AWS_METHODS)
@app.route("/<path:path>", methods=AWS_METHODS)
def proxy(path):
    """
    Intercept AWS calls. Enforce prefix scheduling, record trace,
    optionally DROP, otherwise forward to LocalStack.
    """
    global trace

    client = request.headers.get("X-Client-Id", "unknown")
    thread = request.headers.get("X-Thread-Id", "unknown")
    method = request.method
    full_path = f"/{path}"

    base_step = step_key(client, thread, method, full_path)

    with cond:
        ok = scheduler.wait_for_turn(base_step)
        if not ok:
            # Run ended or not scheduled
            write_sim_log(
                parse_step,
                base_step,
                408,
                "Blocked (run ended / not scheduled)",
                run_index + 1,
                len(trace) + 1,
            )
            return Response("Blocked", status=408)

        matched, was_drop = scheduler.consume_last_match()
        presented = matched if matched is not None else base_step

        scheduler.maybe_stop_enforcing()

        trace.append(presented)
        step_index = len(trace)

        # Optional scheduler log (keeps your earlier debugging signal)
        # We show "got=" if it matched a forced expected element, otherwise "free="
        if scheduler.expected_now() is None:
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

    # Forward outside lock
    try:
        resp = forward_to_localstack(LOCALSTACK_URL, method, path, timeout=10)
        status = resp.status_code

        if current_result["value"] == RUN_SUCCESS:
            fail_reason = classify_http_failure(
                status=status,
                method=method,
                full_path=full_path,
                treat_404_as_fail=TREAT_404_AS_FAIL,
            )
            if fail_reason is not None:
                current_result["value"] = RUN_FAILURE
                failure_reason["value"] = fail_reason

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