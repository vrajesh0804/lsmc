import threading
import time

from src.simcore.scheduler import PrefixScheduler, RUN_SUCCESS, RUN_TIMEOUT


def test_scheduler_enforces_order():
    lock = threading.Lock()
    cond = threading.Condition(lock)
    current = {"value": RUN_SUCCESS}
    reason = {"value": ""}

    sched = PrefixScheduler(cond=cond, deadlock_timeout_s=1,
                           current_result_ref=current, failure_reason_ref=reason)

    with cond:
        sched.start_run(["A", "B"])

    results = []

    def worker(step):
        with cond:
            ok = sched.wait_for_turn(step)
        results.append((step, ok))

    tB = threading.Thread(target=worker, args=("B",))
    tA = threading.Thread(target=worker, args=("A",))

    tB.start()
    time.sleep(0.05)
    tA.start()
    tA.join(2)
    tB.join(2)

    assert ("A", True) in results
    assert ("B", True) in results


def test_watchdog_timeout():
    lock = threading.Lock()
    cond = threading.Condition(lock)
    current = {"value": RUN_SUCCESS}
    reason = {"value": ""}

    sched = PrefixScheduler(cond=cond, deadlock_timeout_s=0.2,
                           current_result_ref=current, failure_reason_ref=reason)
    with cond:
        sched.start_run(["NEVER"])

    time.sleep(0.25)
    with cond:
        sched.watchdog_tick()

    assert current["value"] == RUN_TIMEOUT
    assert "Infeasible forced prefix" in reason["value"]
