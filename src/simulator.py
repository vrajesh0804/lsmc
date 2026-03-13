# src/simulator.py
from flask import Flask, request
from src.simcore.simulator_config import AWS_METHODS, load_config, print_config
from src.simcore.simulator_state import init_state
from src.simcore.simulator_watchdog import start_watchdog_thread
from src.simcore.simulator_prefixes import pick_next_prefix_or_done
from src.simcore.sim_helpers import reset_localstack_quiet
from src.simcore.simulator_handlers import (
    ready as ready_handler,
    execution_done as execution_done_handler,
    proxy as proxy_handler,
)

cfg = load_config()
print_config(cfg)

app = Flask(__name__)
state = init_state(cfg.forced_prefix_deadlock_timeout)

start_watchdog_thread(state)


@app.route("/__ready__", methods=["GET"])
def ready():
    return ready_handler(state)


@app.route("/__execution_done__", methods=["POST"])
def execution_done():
    return execution_done_handler(state, cfg)


@app.route("/", defaults={"path": ""}, methods=AWS_METHODS)
@app.route("/<path:path>", methods=AWS_METHODS)
def proxy(path):
    # IMPORTANT: do NOT add a global lock here.
    # The scheduler in proxy_handler already enforces one-step-at-a-time behavior.
    return proxy_handler(state, cfg, path=path, method=request.method)


with state.cond:
    state.seen_prefixes.add(tuple([]))
    state.pending_prefixes.append([])
    state.scheduler.start_run([])
    reset_localstack_quiet()
    pick_next_prefix_or_done(state)