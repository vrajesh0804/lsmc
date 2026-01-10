import os
import sys
import threading
import time
import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError

SIMULATOR_ENDPOINT = "http://localhost:9998"
CLIENT_ID = "client-A"

# Flag file so we can behave differently on discovery run vs replay runs
DISCOVERY_FLAG = os.path.join(os.path.dirname(__file__), ".discovery_done")

session = boto3.session.Session()


def make_s3(thread_name: str):
    s3 = session.client(
        "s3",
        endpoint_url=SIMULATOR_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(signature_version="s3v4"),
    )

    def inject_headers(model, params, **kwargs):
        params.setdefault("headers", {})
        params["headers"]["X-Client-Id"] = CLIENT_ID
        params["headers"]["X-Thread-Id"] = thread_name

    s3.meta.events.register("before-call.s3", inject_headers)
    return s3


def _is_timeout(e: ClientError) -> bool:
    http_status = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
    code = str(e.response.get("Error", {}).get("Code", "")).strip()
    return http_status == 408 or code in {"408", "RequestTimeout"}


def _short_err(e: ClientError) -> str:
    http_status = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
    code = str(e.response.get("Error", {}).get("Code", "")).strip() or "Unknown"
    msg = str(e.response.get("Error", {}).get("Message", "")).strip() or "No message"
    return f"HTTP {http_status}, Code={code}, {msg}"


def main():
    """
    Discovery run records EXACTLY 3 steps per thread => 20 scheduled executions.

    Thread-1 discovery steps:
      1) create_bucket        (PUT /shared-bucket)
      2) upload key1          (PUT /shared-bucket/key1.txt)
      3) list_buckets         (GET /)

    Thread-2 discovery steps:
      1) list_buckets         (GET /)
      2) head_object key1     (HEAD /shared-bucket/key1.txt)  <-- may FAIL in some schedules later
      3) upload key2          (PUT /shared-bucket/key2.txt)

    Mixed outcomes in replay:
      ✅ SUCCESS: head_object happens after upload(key1)
      ❌ FAILURE: head_object happens before upload(key1) -> 404 NoSuchKey (or similar)
      💥 CRASH: if Thread-2 managed to do its first step before Thread-1 finished create_bucket (schedule-dependent),
               we intentionally exit non-zero at the end (counted once by main.py)
      ⏱ TIMEOUT: only in replay, Thread-1 may execute an EXTRA step not in discovery (GET /shared-bucket?list-type=2)
                => scheduler cannot match it => watchdog => TIMEOUT
    """
    is_discovery = not os.path.exists(DISCOVERY_FLAG)

    bucket = "shared-bucket"

    key1 = "key1.txt"
    key2 = "key2.txt"

    with open(key1, "w", encoding="utf-8") as f:
        f.write("k1\n")
    with open(key2, "w", encoding="utf-8") as f:
        f.write("k2\n")

    created_event = threading.Event()
    upload2_done_event = threading.Event()
    crash_flag = threading.Event()

    # --- Thread-1 ---
    def t1():
        s3 = make_s3("Thread-1")
        try:
            # Step 1 (discovery + replay)
            s3.create_bucket(Bucket=bucket)
            created_event.set()

            # Step 2 (discovery + replay)
            s3.upload_file(key1, bucket, key1)

            # Step 3 (discovery + replay)
            s3.list_buckets()

            # EXTRA step ONLY in replay (to induce TIMEOUT in some schedules)
            # This step is NOT present in discovery's recorded_flow, so when executed it will TIMEOUT.
            if (not is_discovery) and upload2_done_event.is_set():
                try:
                    # list_objects_v2 triggers GET /shared-bucket?list-type=2 (path /shared-bucket)
                    s3.list_objects_v2(Bucket=bucket)
                except ClientError as e:
                    if _is_timeout(e):
                        print("⏱ TIMEOUT (expected in some schedules): extra replay-only step blocked by scheduler.")
                        return
                    print("❌ Thread-1 extra step failed:", _short_err(e))
                    return

        except ClientError as e:
            if _is_timeout(e):
                print("⏱ TIMEOUT: Thread-1 request blocked by scheduler.")
                return
            print("❌ Thread-1 failed:", _short_err(e))

    # --- Thread-2 ---
    def t2():
        s3 = make_s3("Thread-2")
        try:
            # Step 1 (discovery + replay)
            s3.list_buckets()

            # Crash condition (schedule-dependent):
            # If Thread-2 managed to do its first step before Thread-1 finished create_bucket,
            # we treat that schedule as a "crash" demo (exit non-zero at end).
            if not created_event.is_set() and (not is_discovery):
                crash_flag.set()

            # Step 2 (discovery + replay) -- this is what yields FAILURE in some schedules
            # If HEAD happens before Thread-1 upload(key1), LocalStack should return 404-ish.
            try:
                s3.head_object(Bucket=bucket, Key=key1)
            except ClientError as e:
                if _is_timeout(e):
                    print("⏱ TIMEOUT: Thread-2 head_object blocked by scheduler.")
                    return
                # This is our intended FAILURE (do not crash here; allow run to finish)
                print("❌ FAILURE (expected in some schedules): head_object before upload ->", _short_err(e))

            # Step 3 (discovery + replay)
            try:
                s3.upload_file(key2, bucket, key2)
                upload2_done_event.set()
            except ClientError as e:
                if _is_timeout(e):
                    print("⏱ TIMEOUT: Thread-2 upload blocked by scheduler.")
                    return
                print("❌ Thread-2 upload failed:", _short_err(e))
                return

        except ClientError as e:
            if _is_timeout(e):
                print("⏱ TIMEOUT: Thread-2 request blocked by scheduler.")
                return
            print("❌ Thread-2 failed:", _short_err(e))

    th1 = threading.Thread(target=t1, name="Thread-1")
    th2 = threading.Thread(target=t2, name="Thread-2")

    # Start both. No intentional delays; let scheduler determine order.
    th1.start()
    th2.start()
    th1.join()
    th2.join()

    # Mark discovery done only after the first completion
    if is_discovery:
        with open(DISCOVERY_FLAG, "w", encoding="utf-8") as f:
            f.write("done\n")

    # Notify simulator this run finished
    requests.post(f"{SIMULATOR_ENDPOINT}/__execution_done__")

    # Convert some schedules into CRASH (non-zero exit) in a controlled, user-friendly way
    if (not is_discovery) and crash_flag.is_set():
        print("💥 CRASH (expected in some schedules): schedule met crash condition; exiting non-zero.")
        sys.exit(7)


if __name__ == "__main__":
    main()
