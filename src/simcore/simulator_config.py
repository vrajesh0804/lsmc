# src/simcore/simulator_config.py
import os
import logging
from dataclasses import dataclass

logging.getLogger("werkzeug").setLevel(logging.ERROR)

AWS_METHODS = ["GET", "POST", "PUT", "DELETE", "HEAD"]
LOCALSTACK_URL = "http://localhost:9999"

@dataclass(frozen=True)
class SimConfig:
    forced_prefix_deadlock_timeout: int
    treat_404_as_fail: bool
    treat_408_as_timeout: bool
    enable_drop: bool
    enable_delay: bool
    delay_seconds: int
    phase_order: str
    artifact_ok_status: int

def load_config() -> SimConfig:
    forced = int(os.environ.get("SIM_FORCED_PREFIX_TIMEOUT", "60"))

    treat_404 = os.environ.get("SIM_404_AS_FAIL", "0") == "1"
    treat_408 = os.environ.get("SIM_408_AS_TIMEOUT", "0") == "1"
    enable_drop = os.environ.get("SIM_ENABLE_DROP", "0") == "1"
    enable_delay = os.environ.get("SIM_ENABLE_DELAY", "0") == "1"
    delay_s = int(os.environ.get("SIM_DELAY_SECONDS", "60"))

    phase_order = os.environ.get("SIM_PHASE_ORDER", "drop_first").strip().lower()
    if phase_order not in ("drop_first", "delay_first"):
        phase_order = "drop_first"

    artifact_ok = int(os.environ.get("SIM_ARTIFACT_OK_STATUS", "200"))

    return SimConfig(
        forced_prefix_deadlock_timeout=forced,
        treat_404_as_fail=treat_404,
        treat_408_as_timeout=treat_408,
        enable_drop=enable_drop,
        enable_delay=enable_delay,
        delay_seconds=delay_s,
        phase_order=phase_order,
        artifact_ok_status=artifact_ok,
    )

def print_config(cfg: SimConfig) -> None:
    print(f"[SIM] 404 treated as failure: {cfg.treat_404_as_fail}", flush=True)
    print(f"[SIM] 408 treated as timeout: {cfg.treat_408_as_timeout}", flush=True)
    print(f"[SIM] DROP enabled: {cfg.enable_drop}", flush=True)
    print(f"[SIM] DELAY enabled: {cfg.enable_delay} (seconds={cfg.delay_seconds})", flush=True)
    print(f"[SIM] PHASE order: {cfg.phase_order}", flush=True)
    print(f"[SIM] Artifact treated as OK: HTTP {cfg.artifact_ok_status}", flush=True)