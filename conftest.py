import os
import pytest
from tools.run_and_report import run_and_report

REPORT_DIR = "test/reports"
REPORT_FILE = os.path.join(REPORT_DIR, "client_run_report.txt")

CLIENTS = [
    "client/S3/bucket_checked_create_vs_delete_shared_boto.py",
    "client/S3/create_bucket_two_threads_shared_boto.py",
    "client/S3/create_bucket_two_threads_separate_boto.py",
    "client/S3/client_threads_workload.py",
]


def pytest_sessionstart(session):
    """
    Called once when pytest starts.
    Delete old report and prepare a fresh one.
    """
    os.makedirs(REPORT_DIR, exist_ok=True)

    if os.path.exists(REPORT_FILE):
        os.remove(REPORT_FILE)

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("CLIENT EXECUTION REPORT\n")
        f.write("=" * 60 + "\n\n")


def pytest_sessionfinish(session, exitstatus):
    """
    Called once when pytest finishes.
    Run client explorations and write final report.
    """
    with open(REPORT_FILE, "a", encoding="utf-8") as f:
        for client in CLIENTS:
            f.write(f"\nCLIENT: {client}\n")
            f.write("-" * 60 + "\n")

            rc, counts, out, err = run_and_report(client)

            if any(v is None for v in counts.values()):
                f.write("\n[DEBUG] Parsing failed: main.py did not produce expected FINAL SUMMARY.\n")
                f.write("[DEBUG] --- STDOUT (first 200 lines) ---\n")
                f.write("\n".join(out.splitlines()[:200]) + "\n")
                f.write("[DEBUG] --- STDERR (first 200 lines) ---\n")
                f.write("\n".join(err.splitlines()[:200]) + "\n")

            f.write(f"Exit code : {rc}\n")
            f.write(f"Success   : {counts['success']}\n")
            f.write(f"Failure   : {counts['failure']}\n")
            f.write(f"Crash     : {counts['crash']}\n")
            f.write(f"Timeout   : {counts['timeout']}\n")
