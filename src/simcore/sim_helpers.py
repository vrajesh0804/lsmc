from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Dict, List, Tuple

from src.simcore.dpor import DELAY_PREFIX, delay_seconds, undelay


def step_key(client: str, thread: str, method: str, path: str) -> str:
    return f"{client}:{thread}:{method}:{path}"


def s3_pretty_action(method: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path

    if method == "GET" and path == "/":
        return "list_buckets()"

    if path.count("/") == 1:
        bucket = path[1:]
        if method == "PUT":
            return f"create_bucket({bucket})"
        if method == "HEAD":
            return f"head_bucket({bucket})"
        if method == "DELETE":
            return f"delete_bucket({bucket})"
        if method == "GET":
            return f"list_objects(bucket={bucket})"

    parts = path.split("/", 2)
    bucket = parts[1] if len(parts) > 1 else "?"
    key = parts[2] if len(parts) > 2 else "?"

    if method == "PUT":
        return f"put_object({bucket}, key={key})"
    if method == "GET":
        return f"get_object({bucket}, key={key})"
    if method == "HEAD":
        return f"head_object({bucket}, key={key})"
    if method == "DELETE":
        return f"delete_object({bucket}, key={key})"

    return f"{method} {path}"


def pretty_step(step: str, parse_step: Callable[[str], Dict]) -> str:
    info = parse_step(step)
    action = s3_pretty_action(info["method"], info["path"])
    base = f"{info['client']} | {info['thread']} | {action}"

    prefix = ""
    if info.get("is_delay"):
        sec = info.get("delay_seconds")
        prefix += f"DELAY({sec}) " if sec is not None else "DELAY "
    if info.get("is_drop"):
        prefix += "DROP "

    return f"{prefix}{base}".strip()


def print_prefix(prefix: List[str], parse_step: Callable[[str], Dict]) -> None:
    if not prefix:
        print("\n📌 Forced prefix: (empty) — free run / discovery")
        return

    print(f"\n📌 Forced prefix (length={len(prefix)})")
    for i, s in enumerate(prefix, start=1):
        print(f"  {i:02d}. {pretty_step(s, parse_step)}")


def classify_http_failure(
    *,
    status: int,
    method: str,
    full_path: str,
    treat_404_as_fail: bool,
    treat_408_as_timeout: bool,
) -> tuple[str, str] | None:
    """
    Returns:
      None                      -> count as success
      ("FAILURE", reason)      -> count as failure
      ("TIMEOUT", reason)      -> count as timeout
    """
    if status == 404 and not treat_404_as_fail:
        return None

    if status == 408:
        if treat_408_as_timeout:
            return ("TIMEOUT", f"{method} {full_path} → 408")
        return None

    if status >= 400:
        return ("FAILURE", f"{method} {full_path} → {status}")

    return None


def reset_localstack_quiet() -> None:
    try:
        script_dir = os.path.dirname(os.path.dirname(__file__))
        script = os.path.join(script_dir, "reset_localstack.py")
        subprocess.run(
            [sys.executable, script],
            timeout=20,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("[SIM] LocalStack reset complete", flush=True)
    except Exception as e:
        print(f"[SIM] LocalStack reset failed: {e}", flush=True)


def print_final_summary(
    results: List[Tuple[str, str]],
    *,
    run_success: str,
    run_failure: str,
    run_crash: str,
    run_timeout: str,
    run_infeasible: str,
) -> None:
    def count(kind: str) -> int:
        return sum(1 for r, _ in results if r == kind)

    print("\n📊 FINAL SUMMARY")
    print(f"  ✅ Success     : {count(run_success)}")
    print(f"  ❌ Failure     : {count(run_failure)}")
    print(f"  💥 Crash       : {count(run_crash)}")
    print(f"  ⏱ Timeout     : {count(run_timeout)}")
    print(f"  🚫 Infeasible  : {count(run_infeasible)}")