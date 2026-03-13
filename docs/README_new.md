# Python-Based Turmoil Simulation Framework for Testing AWS Cloud Interactions via LocalStack

This repository contains a **Flask-based simulator/proxy** in front of **LocalStack** (S3 + DynamoDB support in reset) that **systematically explores concurrent interleavings** of multithreaded AWS client workloads using **forced prefixes** and a **DPOR-style prefix generator**.

The simulator identifies each incoming HTTP request as a **step** using client-injected headers:

- `X-Client-Id`
- `X-Thread-Id`

and then schedules those steps deterministically (free runs) or according to a forced prefix (replay/exploration).

---

## What this project does

- Runs a client workload repeatedly until exploration is done.
- For each run:
  - Schedules interleavings via `PrefixScheduler`
  - Records a trace of steps
  - Generates new forced prefixes (swaps / DROP / DELAY) from that trace
- Supports two exploration mutations:
  - **DROP**: intentionally skip a step (simulator returns an artifact response)
  - **DELAY**: inject a strict delay window before forwarding a step to LocalStack

At the end, it prints a **FINAL SUMMARY** with counts.

---

## Requirements

- Python 3.x
- LocalStack running at `http://localhost:9999`
- Python deps (typical): `flask`, `requests`, `boto3`, `botocore`, `pytest`

---

## Quick start

### 1) Start LocalStack
Ensure LocalStack is running and reachable on port 9999.

### 2) Run a client workload
```bash
python main.py <client_file>.py
```
---

## Command-line arguments (main.py)

### `--drop`
Enable DROP exploration (DPOR will generate prefixes that drop steps).
```bash
python main.py --drop <client_file>.py
```

### `--delay`
Enable DELAY exploration (DPOR will generate prefixes that delay steps).
Default - 20 seconds
```bash
python main.py --delay <client_file>.py
```

### `--delay-for-<N>`
If `--delay` is enabled, override the delay duration to **N seconds**.
```bash
python main.py --delay --delay-for-N <client_file>.py
```

### `--404-as-fail`
Treat HTTP 404 responses as failures (otherwise 404 can be ignored depending on context).
```bash
python main.py --404-as-fail <client_file>.py
```

### `--408-as-timeout`
Treat HTTP 408 responses as timeout (otherwise 408 can be ignored depending on context).
```bash
python main.py --408-as-timeout <client_file>.py
```
---

## Output and results

During execution you will see scheduler and step logs (e.g. `[SCHED]`, `[STEP]`, `[DROP]`, `[DELAY]`).  
At the end you will see something like:

```
📊 FINAL SUMMARY
  ✅ Success     : N
  ❌ Failure     : N
  💥 Crash       : N
  ⏱ Timeout     : N
```

### How these results happen (in this codebase)

- **Success**: no client error reported and no simulator-side failure classification occurred.
- **Failure**: simulator classified an HTTP failure (>=400, configurable for 404) OR client reported a non-timeout error.
- **Crash**: kept for compatibility, but current flow treats client crash as **Timeout** in `__execution_done__`.
- **Timeout**:
  - forced-prefix enforcement watchdog triggers while waiting for a required step, OR
  - forwarding to LocalStack raises an exception, OR
  - the client reports a timeout/error that matches timeout markers.

---

## Project layout (current)

```
main.py
src/
  simulator.py
  simulator_logger.py
  reset_localstack.py
  simcore/
    dpor.py
    http_proxy.py
    scheduler.py
    sim_helpers.py
    sim_log.py
    simulator_cache.py
    simulator_config.py
    simulator_handlers.py
    simulator_prefixes.py
    simulator_state.py
    simulator_watchdog.py
tests/
```

### Key modules

- `main.py`
  - Parses CLI args, sets env vars, starts simulator thread, runs the client repeatedly, and notifies the simulator when each run is done.
- `src/simulator.py`
  - Flask app entrypoint; wires handlers and initializes exploration state.
- `src/simcore/simulator_handlers.py`
  - Core proxy/scheduling logic: step identity, retry/cache handling, DROP/DELAY behavior, forwarding to LocalStack, and per-run bookkeeping.
- `src/simcore/scheduler.py`
  - `PrefixScheduler`: deterministic free runs + forced prefix enforcement + watchdog timeouts.
- `src/simcore/dpor.py`
  - Forced prefix generation: adjacent swaps, DROP mutations, DELAY mutations, and mixed DROP+DELAY on different steps.
- `reset_localstack.py` + `src/simcore/sim_helpers.py`
  - Best-effort reset of LocalStack S3 buckets and DynamoDB tables before runs.

---

## Writing a new client workload

1. Use boto3/botocore and register a `before-call.s3` hook.
2. Inject headers for scheduling:
   - `X-Client-Id` (stable per client script)
   - `X-Thread-Id` (thread name)
3. Run multiple threads that issue AWS operations (S3/DynamoDB).

Without those headers, the simulator will label steps as `unknown` and scheduling becomes meaningless.

---

## Testing

Run all tests:
```bash
pytest -v
```

Run one file:
```bash
pytest tests/<path>.py -vv -s
```

---

## Troubleshooting

- **Simulator not reachable**: confirm `http://localhost:9998/__ready__` responds and ports are free.
- **LocalStack errors**: confirm LocalStack is on `http://localhost:9999` and healthy.

---
