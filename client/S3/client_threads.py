import os
import time
import threading
import boto3
import requests
from botocore.config import Config

SIMULATOR_ENDPOINT = "http://localhost:9998"

FILES = {
    "Thread-1": "file1.txt",
    "Thread-2": "file2.txt",
}

CLIENT_ID = "client-A"   # single client, multiple threads

START_DELAY = {
    "Thread-1": 2,  # intentional skew to show pause/resume
}

session = boto3.session.Session()


def ensure_files():
    for f in FILES.values():
        if not os.path.exists(f):
            with open(f, "w") as file:
                file.write(f"Dummy content for {f}\n")


def make_client(thread_name):
    client = session.client(
        "s3",
        endpoint_url=SIMULATOR_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(signature_version="s3v4"),
    )

    def inject_headers(model, params, **kwargs):
        headers = params.setdefault("headers", {})
        headers["X-Client-Id"] = CLIENT_ID
        headers["X-Thread-Id"] = thread_name

    client.meta.events.register("before-call.s3", inject_headers)
    return client


def flow(thread_name):
    bucket = f"{thread_name.lower()}-bucket"
    file_name = FILES[thread_name]

    # time.sleep(START_DELAY.get(thread_name, 0))

    s3 = make_client(thread_name)

    print(f"[{thread_name}] ▶️ start")

    s3.create_bucket(Bucket=bucket)
    print(f"[{thread_name}] ✅ create_bucket")

    s3.list_buckets()
    print(f"[{thread_name}] 📋 list_buckets")

    s3.upload_file(file_name, bucket, file_name)
    print(f"[{thread_name}] 📤 upload_file")

    print(f"[{thread_name}] 🏁 done\n")


if __name__ == "__main__":
    ensure_files()

    threads = [
        threading.Thread(target=flow, args=(t,))
        for t in FILES
    ]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    requests.post(f"{SIMULATOR_ENDPOINT}/__execution_done__")
    print("✅ execution_done sent")
