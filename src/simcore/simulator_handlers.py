# src/simcore/simulator_handlers.py
from flask import request, Response
from typing import List, Tuple
from collections import defaultdict
import time

from src.simcore.http_proxy import forward_to_localstack
from src.simcore.sim_log import write_sim_log
from src.simcore.scheduler import RUN_SUCCESS, RUN_FAILURE, RUN_CRASH, RUN_TIMEOUT
from src.simcore.dpor import parse_step, generate_forced_prefixes
from src.simcore.sim_helpers import (
    step_key,
    pretty_step,
    classify_http_failure,
    reset_localstack_quiet,
)

from src.simcore.simulator_cache import artifact_response_for, cache_response
from src.simcore.simulator_prefixes import pick_next_prefix_or_done, prefix_sort_key


def print_final_summary(results_list: List[Tuple[str, str]]) -> None:
    success = sum(1 for r, _ in results_list if r == RUN_SUCCESS)
    failure = sum(1 for r, _ in results_list if r == RUN_FAILURE)
    crash = sum(1 for r, _ in results_list if r == RUN_CRASH)
    timeout = sum(1 for r, _ in results_list if r == RUN_TIMEOUT)

    print("\n📊 FINAL SUMMARY", flush=True)
    print(f"  ✅ Success     : {success}", flush=True)
    print(f"  ❌ Failure     : {failure}", flush=True)
    print(f"  💥 Crash       : {crash}", flush=True)
    print(f"  ⏱ Timeout     : {timeout}", flush=True)


def ready(state):
    return ("DONE", 200) if state.done else ("READY", 200)


def execution_done(state, cfg):
    payload = request.get_json(silent=True) or {}
    is_crash = bool(payload.get("crash"))
    exit_code = payload.get("exit_code")

    client_had_error = bool(payload.get("client_had_error", False))
    client_error_kind = payload.get("client_error_kind")
    client_error_detail = (payload.get("client_error_detail") or "").strip()

    with state.cond:
        state.run_index += 1

        # CRASH -> TIMEOUT if SUCCESS
        if is_crash and state.current_result["value"] == RUN_SUCCESS:
            state.current_result["value"] = RUN_TIMEOUT
            state.failure_reason["value"] = f"Client crash treated as timeout (exit code {exit_code})"

        if (not is_crash) and client_had_error and state.current_result["value"] == RUN_SUCCESS:
            if client_error_kind == "TIMEOUT":
                state.current_result["value"] = RUN_TIMEOUT
                state.failure_reason["value"] = client_error_detail or "Client timed out"
            else:
                state.current_result["value"] = RUN_FAILURE
                state.failure_reason["value"] = client_error_detail or "Client reported failure"

        status = state.current_result["value"]
        reason = state.failure_reason["value"]

        state.explored_prefixes.add(tuple(state.forced_prefix))
        trace_tuple = tuple(state.trace)

        # HARD TRACE DEDUPE includes TIMEOUT
        if status in (RUN_SUCCESS, RUN_FAILURE, RUN_TIMEOUT) and trace_tuple in state.seen_traces:
            _reset_for_next_run(state)
            state.scheduler.start_run([])
            reset_localstack_quiet()
            pick_next_prefix_or_done(state)

            if state.done:
                print_final_summary(state.results)
                print("✅ All executions explored.", flush=True)

            state.cond.notify_all()
            return ("OK", 200)

        state.results.append((status, reason))

        if status == RUN_SUCCESS:
            print(f"✅ Execution {state.run_index}: SUCCESS", flush=True)
        elif status == RUN_FAILURE:
            print(f"❌ Execution {state.run_index}: FAILURE — {reason}", flush=True)
        elif status == RUN_CRASH:
            print(f"💥 Execution {state.run_index}: CRASH — {reason}", flush=True)
        else:
            print(f"⏱ Execution {state.run_index}: TIMEOUT — {reason}", flush=True)

        new_prefixes: List[List[str]] = []

        if status in (RUN_SUCCESS, RUN_FAILURE, RUN_TIMEOUT):
            state.seen_traces.add(trace_tuple)
            for k in range(1, len(trace_tuple) + 1):
                state.explored_trace_prefixes.add(trace_tuple[:k])

            all_new = generate_forced_prefixes(
                state.trace,
                state.seen_prefixes,
                state.explored_prefixes,
                enable_drop=cfg.enable_drop,
                enable_delay=cfg.enable_delay,
                delay_s=cfg.delay_seconds,
            )

            filtered: List[List[str]] = []
            for p in all_new:
                tp = tuple(p)
                if tp in state.explored_prefixes:
                    continue
                if tp in state.explored_trace_prefixes:
                    continue
                filtered.append(p)

            new_prefixes = sorted(filtered, key=lambda p: prefix_sort_key(p, cfg.phase_order))
            for p in new_prefixes:
                state.pending_prefixes.append(p)

        _reset_for_next_run(state)
        state.scheduler.start_run([])
        reset_localstack_quiet()
        pick_next_prefix_or_done(state)

        print(f"\n🧠 Discovered {len(new_prefixes)} new prefixes", flush=True)
        print(f"📦 Pending prefixes: {len(state.pending_prefixes)}", flush=True)

        if state.done:
            print_final_summary(state.results)
            print("✅ All executions explored.", flush=True)

        state.cond.notify_all()

    return ("OK", 200)


def _reset_for_next_run(state):
    state.trace = []
    state.current_result["value"] = RUN_SUCCESS
    state.failure_reason["value"] = ""

    state.seen_base_steps_this_run = set()
    state.step_attempts_this_run = defaultdict(int)

    state.response_cache_this_run = {}
    state.inflight_steps_this_run = set()

    # ✅ DELAY coordination flag (fixes --delay timeouts)
    state.delay_active = False


def proxy(state, cfg, path: str, method: str):
    client = request.headers.get("X-Client-Id", "unknown")
    thread = request.headers.get("X-Thread-Id", "unknown")
    full_path = f"/{path}"
    base_step = step_key(client, thread, method, full_path)

    do_delay_s = 0
    presented = base_step
    step_index = 0

    with state.cond:
        state.step_attempts_this_run[base_step] += 1
        attempt_no = state.step_attempts_this_run[base_step]

        # If some other step is currently in its STRICT delay window, block everyone else.
        while state.delay_active:
            state.cond.wait(timeout=0.1)

        # RETRY handling
        if base_step in state.seen_base_steps_this_run:
            while base_step in state.inflight_steps_this_run and base_step not in state.response_cache_this_run:
                state.cond.wait(timeout=0.1)

            if base_step in state.response_cache_this_run:
                status, body, hdrs = state.response_cache_this_run[base_step]
                print(
                    f"[RETRY] run={state.run_index+1} attempt={attempt_no} "
                    f"duplicate -> returning cached {status}: {pretty_step(base_step, parse_step)}",
                    flush=True,
                )
                write_sim_log(parse_step, base_step, status, "Retry served from cache", state.run_index + 1, len(state.trace) + 1)
                return Response(body, status=status, headers=hdrs)

            return artifact_response_for(base_step, state.stable_response_cache, cfg.artifact_ok_status)

        ok = state.scheduler.wait_for_turn(base_step)
        if not ok:
            msg = "SIM_FORCED_PREFIX_TIMEOUT"
            state.response_cache_this_run[base_step] = (503, msg.encode("utf-8"), {"Content-Type": "text/plain"})
            state.stable_response_cache.setdefault(base_step, (503, msg.encode("utf-8"), {"Content-Type": "text/plain"}))
            state.cond.notify_all()
            return Response(msg, status=503)

        matched, was_drop, was_delay, matched_delay_s = state.scheduler.consume_last_match()
        presented = matched if matched is not None else base_step

        state.seen_base_steps_this_run.add(base_step)
        state.inflight_steps_this_run.add(base_step)

        state.trace.append(presented)
        step_index = len(state.trace)

        if state.scheduler.expected_now() is None:
            print(f"[SCHED] run={state.run_index+1} free={pretty_step(presented, parse_step)}", flush=True)
        else:
            print(f"[SCHED] run={state.run_index+1} got={pretty_step(presented, parse_step)}", flush=True)

        # DROP: unchanged
        if was_drop:
            print(f"[DROP] run={state.run_index+1} {pretty_step(presented, parse_step)}", flush=True)
            write_sim_log(parse_step, base_step, cfg.artifact_ok_status, "Dropped treated as OK", state.run_index + 1, step_index)
            state.scheduler.maybe_stop_enforcing()
            state.inflight_steps_this_run.discard(base_step)
            state.cond.notify_all()
            return artifact_response_for(base_step, state.stable_response_cache, cfg.artifact_ok_status)

        # ---- STRICT DELAY (the important part) ----
        do_delay_s = matched_delay_s if (was_delay and matched_delay_s > 0) else 0
        if do_delay_s > 0:
            # Mark delay active so *all other steps block* until this one completes.
            state.delay_active = True
            print(
                f"[DELAY] run={state.run_index+1} step={step_index} STRICT pause {do_delay_s}s -> "
                f"{pretty_step(presented, parse_step)}",
                flush=True,
            )

    # Phase 2: sleep outside lock (others are blocked by delay_active=True)
    if do_delay_s > 0:
        time.sleep(do_delay_s)
        print(f"[DELAY] run={state.run_index+1} step={step_index} delay finished -> forwarding now", flush=True)

    try:
        resp = forward_to_localstack("http://localhost:9999", method, path, timeout=10)
        status = resp.status_code
        hdrs = {k: v for k, v in resp.headers.items()}
        body = resp.content

        with state.cond:
            if state.current_result["value"] == RUN_SUCCESS:
                classified = classify_http_failure(
                    status=status,
                    method=method,
                    full_path=full_path,
                    treat_404_as_fail=cfg.treat_404_as_fail,
                    treat_408_as_timeout=cfg.treat_408_as_timeout,
                )
                if classified is not None:
                    kind, reason = classified
                    state.current_result["value"] = RUN_TIMEOUT if kind == "TIMEOUT" else RUN_FAILURE
                    state.failure_reason["value"] = reason

            write_sim_log(parse_step, base_step, status, f"HTTP {status}", state.run_index + 1, step_index)

            label = pretty_step(base_step, parse_step)
            outcome = "OK" if status < 400 else f"HTTP {status}"
            print(f"[STEP] run={state.run_index+1} step={step_index} {label} -> {outcome} (LocalStack={status})", flush=True)

            state.response_cache_this_run[base_step] = (status, body, hdrs)
            state.stable_response_cache.setdefault(base_step, (status, body, hdrs))

            state.scheduler.maybe_stop_enforcing()

            state.inflight_steps_this_run.discard(base_step)

            # Release STRICT delay gate
            if do_delay_s > 0:
                state.delay_active = False

            state.cond.notify_all()

        return Response(body, status, resp.headers)

    except Exception as e:
        with state.cond:
            if state.current_result["value"] == RUN_SUCCESS and cfg.treat_408_as_timeout:
                state.current_result["value"] = RUN_TIMEOUT
                state.failure_reason["value"] = f"LocalStack/forwarding exception: {type(e).__name__}"

            print(
                f"[STEP] run={state.run_index+1} step={step_index} "
                f"{pretty_step(base_step, parse_step)} -> TIMEOUT/EXCEPTION",
                flush=True,
            )

            write_sim_log(parse_step, base_step, 408, "Timeout (forwarding exception)", state.run_index + 1, step_index)

            state.inflight_steps_this_run.discard(base_step)

            # Release STRICT delay gate even on exception
            if do_delay_s > 0:
                state.delay_active = False

            state.cond.notify_all()

        return Response("Timeout", status=408)