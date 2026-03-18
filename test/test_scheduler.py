import threading
import time

from src.simcore.scheduler import PrefixScheduler, RUN_SUCCESS, RUN_TIMEOUT


def test_scheduler_enforces_order():
    lock = threading.Lock()
    cond = threading.Condition(lock)
    current = {"value": RUN_SUCCESS}
    reason = {"value": ""}

    sched = PrefixScheduler(
        cond=cond,
        deadlock_timeout_s=1,
        current_result_ref=current,
        failure_reason_ref=reason,
    )

    with cond:
        sched.start_run(["A", "B"])

    results = []

    def worker(step):
        with cond:
            ok = sched.wait_for_turn(step)
        results.append((step, ok))

    t_b = threading.Thread(target=worker, args=("B",))
    t_a = threading.Thread(target=worker, args=("A",))

    t_b.start()
    time.sleep(0.05)
    t_a.start()
    t_a.join(2)
    t_b.join(2)

    assert ("A", True) in results
    assert ("B", True) in results


def test_watchdog_timeout_sets_timeout_and_reason():
    lock = threading.Lock()
    cond = threading.Condition(lock)
    current = {"value": RUN_SUCCESS}
    reason = {"value": ""}

    sched = PrefixScheduler(
        cond=cond,
        deadlock_timeout_s=0.2,
        current_result_ref=current,
        failure_reason_ref=reason,
    )

    with cond:
        sched.start_run(["NEVER"])

    time.sleep(0.25)
    with cond:
        sched.watchdog_tick()

    assert current["value"] == RUN_TIMEOUT
    assert "Timeout while enforcing forced prefix" in reason["value"]


def test_scheduler_allows_free_run_when_prefix_empty():
    lock = threading.Lock()
    cond = threading.Condition(lock)
    current = {"value": RUN_SUCCESS}
    reason = {"value": ""}

    sched = PrefixScheduler(
        cond=cond,
        deadlock_timeout_s=1,
        current_result_ref=current,
        failure_reason_ref=reason,
    )

    with cond:
        sched.start_run([])

    with cond:
        ok = sched.wait_for_turn("ANY_STEP")

    assert ok is True