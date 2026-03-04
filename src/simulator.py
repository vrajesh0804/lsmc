from flask import Flask, request, Response
import os
import threading
import time
import logging
from collections import deque, defaultdict
from typing import List, Tuple, Set, Dict

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
print(f"[SIM] 404 treated as failure: {TREAT_404_AS_FAIL}", flush=True)

ENABLE_DROP = os.environ.get("SIM_ENABLE_DROP", "0") == "1"
print(f"[SIM] DROP enabled: {ENABLE_DROP}", flush=True)

ENABLE_DELAY = os.environ.get("SIM_ENABLE_DELAY", "0") == "1"
DELAY_SECONDS = int(os.environ.get("SIM_DELAY_SECONDS", "60"))
print(f"[SIM] DELAY enabled: {ENABLE_DELAY} (seconds={DELAY_SECONDS})", flush=True)

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
explored_trace_prefixes: Set[Tuple[str, ...]] = set()

results: List[Tuple[str, str]] = []

# Per-run: first-time executed base steps
seen_base_steps_this_run: Set[str] = set()

# Per-run: how many times each base_step arrived (attempts=1 => no retry)
step_attempts_this_run: Dict[str, int] = defaultdict(int)

# Per-run: response cache so client retries get SUCCESS (no simulator-created 408)
# base_step -> (status_code, body_bytes, headers_dict)
response_cache_this_run: Dict[str, Tuple[int, bytes, Dict[str, str]]] = {}

# Per-run: track which base steps are currently being executed/forwarded
inflight_steps_this_run: Set[str] = set()

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

    while pending_prefixes:
        candidate = pending_prefixes.popleft()
        tp = tuple(candidate)

        if tp in explored_prefixes:
            continue
        if tp in explored_trace_prefixes:
            continue

        forced_prefix = candidate
        scheduler.start_run(forced_prefix)
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

    NOTE: We expand DPOR also from TIMEOUT traces (not from CRASH) so that exploration
    still reaches full DELAY-variant space even if forwarding/localstack times out.
    """
    global run_index, trace, done, forced_prefix
    global seen_base_steps_this_run, step_attempts_this_run
    global response_cache_this_run, inflight_steps_this_run

    payload = request.get_json(silent=True) or {}
    is_crash = bool(payload.get("crash"))
    exit_code = payload.get("exit_code")

    client_had_error = bool(payload.get("client_had_error", False))
    client_error_kind = payload.get("client_error_kind")  # "TIMEOUT"/"FAILURE"/None
    client_error_detail = (payload.get("client_error_detail") or "").strip()

    with cond:
        run_index += 1

        # Highest priority: crash
        if is_crash and current_result["value"] == RUN_SUCCESS:
            current_result["value"] = RUN_CRASH
            failure_reason["value"] = f"Client crashed (exit code {exit_code})"

        # Next: client-reported error overrides SUCCESS
        # (Now that retries are served from cache, simulator-induced 408 won't trigger this.)
        if (not is_crash) and client_had_error and current_result["value"] == RUN_SUCCESS:
            if client_error_kind == "TIMEOUT":
                current_result["value"] = RUN_TIMEOUT
                failure_reason["value"] = client_error_detail or "Client timed out"
            else:
                current_result["value"] = RUN_FAILURE
                failure_reason["value"] = client_error_detail or "Client reported failure"

        status = current_result["value"]
        reason = failure_reason["value"]

        explored_prefixes.add(tuple(forced_prefix))
        trace_tuple = tuple(trace)

        # HARD TRACE DEDUPE (silent) includes TIMEOUT too
        if status in (RUN_SUCCESS, RUN_FAILURE, RUN_TIMEOUT) and trace_tuple in seen_traces:
            trace = []
            current_result["value"] = RUN_SUCCESS
            failure_reason["value"] = ""
            seen_base_steps_this_run = set()
            step_attempts_this_run = defaultdict(int)
            response_cache_this_run = {}
            inflight_steps_this_run = set()

            scheduler.start_run([])
            reset_localstack_quiet()
            _pick_next_prefix_or_done()
            cond.notify_all()
            return ("OK", 200)

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

        # Expand DPOR from SUCCESS/FAILURE/TIMEOUT (NOT crash)
        if status in (RUN_SUCCESS, RUN_FAILURE, RUN_TIMEOUT):
            seen_traces.add(trace_tuple)
            for k in range(1, len(trace_tuple) + 1):
                explored_trace_prefixes.add(trace_tuple[:k])

            all_new = generate_forced_prefixes(
                trace,
                seen_prefixes,
                explored_prefixes,
                enable_drop=ENABLE_DROP,
                enable_delay=ENABLE_DELAY,
                delay_s=DELAY_SECONDS,
            )

            filtered: List[List[str]] = []
            for p in all_new:
                tp = tuple(p)
                if tp in explored_prefixes:
                    continue
                if tp in explored_trace_prefixes:
                    continue
                filtered.append(p)

            new_prefixes = sorted(filtered, key=lambda x: (len(x), tuple(x)))
            for p in new_prefixes:
                pending_prefixes.append(p)

        # reset for next run
        trace = []
        current_result["value"] = RUN_SUCCESS
        failure_reason["value"] = ""
        seen_base_steps_this_run = set()
        step_attempts_this_run = defaultdict(int)
        response_cache_this_run = {}
        inflight_steps_this_run = set()

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
    global trace, seen_base_steps_this_run, step_attempts_this_run
    global response_cache_this_run, inflight_steps_this_run

    client = request.headers.get("X-Client-Id", "unknown")
    thread = request.headers.get("X-Thread-Id", "unknown")
    method = request.method
    full_path = f"/{path}"

    base_step = step_key(client, thread, method, full_path)

    with cond:
        # Count arrivals for debug
        step_attempts_this_run[base_step] += 1
        attempt_no = step_attempts_this_run[base_step]

        # If this is a retry/duplicate, return cached SUCCESS instead of simulator 408.
        if base_step in seen_base_steps_this_run:
            # If the first attempt is still executing, wait until it's cached
            while base_step in inflight_steps_this_run and base_step not in response_cache_this_run:
                cond.wait(timeout=0.1)

            if base_step in response_cache_this_run:
                status, body, hdrs = response_cache_this_run[base_step]
                print(
                    f"[RETRY] run={run_index+1} attempt={attempt_no} duplicate -> returning cached {status}: "
                    f"{pretty_step(base_step, parse_step)}",
                    flush=True,
                )
                # Optional: log retry served (not a failure)
                write_sim_log(
                    parse_step,
                    base_step,
                    status,
                    "Retry served from cache",
                    run_index + 1,
                    len(trace) + 1,
                )
                return Response(body, status=status, headers=hdrs)

            # Extremely rare fallback: no cache (do not fail the run)
            print(
                f"[RETRY] run={run_index+1} attempt={attempt_no} duplicate but no cache yet -> returning 200 empty",
                flush=True,
            )
            return Response(b"", status=200)

        ok = scheduler.wait_for_turn(base_step)
        if not ok:
            # This can happen if run already ended; return 408 (client may treat as timeout)
            # If you want to NEVER fail client here, we can also make this 200. For now keep as-is.
            write_sim_log(
                parse_step,
                base_step,
                408,
                "Blocked (run ended / not scheduled)",
                run_index + 1,
                len(trace) + 1,
            )
            return Response("Blocked", status=408)

        matched, was_drop, was_delay, matched_delay_s = scheduler.consume_last_match()
        presented = matched if matched is not None else base_step

        scheduler.maybe_stop_enforcing()

        # First time executing this step in this run
        seen_base_steps_this_run.add(base_step)
        inflight_steps_this_run.add(base_step)

        trace.append(presented)
        step_index = len(trace)

        if scheduler.expected_now() is None:
            print(f"[SCHED] run={run_index+1} free={pretty_step(presented, parse_step)}", flush=True)
        else:
            print(f"[SCHED] run={run_index+1} got={pretty_step(presented, parse_step)}", flush=True)

        # DROP
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
            inflight_steps_this_run.discard(base_step)
            cond.notify_all()
            return Response("Dropped", status=408)

        # DELAY (STRICT SYNC): hold cond for entire delay + forwarding => other threads wait
        if was_delay and matched_delay_s > 0:
            print(
                f"[DELAY] run={run_index+1} step={step_index} STRICT SYNC pause {matched_delay_s}s -> "
                f"{pretty_step(presented, parse_step)}",
                flush=True,
            )

            time.sleep(matched_delay_s)

            print(
                f"[DELAY] run={run_index+1} step={step_index} delay finished -> forwarding now",
                flush=True,
            )

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
                print(
                    f"[STEP] run={run_index+1} step={step_index} {label} -> {outcome} (LocalStack={status})",
                    flush=True,
                )

                # Cache successful (or non-408) response for retries
                hdrs = {k: v for k, v in resp.headers.items()}
                response_cache_this_run[base_step] = (status, resp.content, hdrs)
                inflight_steps_this_run.discard(base_step)
                cond.notify_all()

                return Response(resp.content, status, resp.headers)

            except Exception as e:
                # Only LocalStack/forwarding exceptions set TIMEOUT
                if current_result["value"] == RUN_SUCCESS:
                    current_result["value"] = RUN_TIMEOUT
                    failure_reason["value"] = f"LocalStack/forwarding exception: {type(e).__name__}"

                write_sim_log(
                    parse_step,
                    base_step,
                    408,
                    "Timeout (forwarding exception)",
                    run_index + 1,
                    step_index,
                )

                label = pretty_step(base_step, parse_step)
                print(
                    f"[STEP] run={run_index+1} step={step_index} {label} -> TIMEOUT/EXCEPTION (forward failed)",
                    flush=True,
                )

                inflight_steps_this_run.discard(base_step)
                cond.notify_all()
                return Response("Timeout", status=408)

        # NORMAL step
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
            print(
                f"[STEP] run={run_index+1} step={step_index} {label} -> {outcome} (LocalStack={status})",
                flush=True,
            )

            # Cache response for retries
            hdrs = {k: v for k, v in resp.headers.items()}
            response_cache_this_run[base_step] = (status, resp.content, hdrs)
            inflight_steps_this_run.discard(base_step)
            cond.notify_all()

            return Response(resp.content, status, resp.headers)

        except Exception:
            # Only LocalStack/forwarding exceptions set TIMEOUT
            if current_result["value"] == RUN_SUCCESS:
                current_result["value"] = RUN_TIMEOUT
                failure_reason["value"] = "LocalStack/forwarding exception"

            write_sim_log(
                parse_step,
                base_step,
                408,
                "Timeout (forwarding exception)",
                run_index + 1,
                step_index,
            )

            inflight_steps_this_run.discard(base_step)
            cond.notify_all()
            return Response("Timeout", status=408)


with cond:
    seen_prefixes.add(tuple([]))
    pending_prefixes.append([])
    _pick_next_prefix_or_done()