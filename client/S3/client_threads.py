import threading
import boto3
import os
import random
from botocore.config import Config
import uuid

SIMULATOR_ENDPOINT = "http://localhost:9998"

# Files for each thread
FILES = {
    "Thread-1": "file1.txt",
    "Thread-2": "file2.txt",
}

THREAD_IDENTITIES = {
    "Thread-1": f"client-{uuid.uuid4().hex[:6]}",
    "Thread-2": f"client-{uuid.uuid4().hex[:6]}",
}

# Ensure files exist
for fname in FILES.values():
    if not os.path.isfile(fname):
        with open(fname, "w") as f:
            f.write(f"This is {fname} for upload.\n")

# Create a base S3 client
session = boto3.session.Session()
base_s3_client = session.client(
    "s3",
    endpoint_url=SIMULATOR_ENDPOINT,
    aws_access_key_id="test",
    aws_secret_access_key="test",
    config=Config(retries={"max_attempts": 3, "mode": "standard"}),
)

def make_thread_client(thread_name: str, logical_port: int):
    client_id = THREAD_IDENTITIES[thread_name]

    s3_client = session.client(
        "s3",
        endpoint_url=SIMULATOR_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )

    def add_headers(model, params, request_signer, **kwargs):
        headers = params.setdefault("headers", {})
        headers["X-Thread-Port"] = str(logical_port)
        headers["X-Client-Id"] = client_id

    s3_client.meta.events.register("before-call.s3", add_headers)

    print(f"[{thread_name}] 🆔 Using Client ID: {client_id}")

    return s3_client

def client_flow(thread_name):
    bucket_name = f"{thread_name.lower()}-bucket"
    file_name = FILES[thread_name]

    # Generate a random dynamic port for this thread (example range 40000–60000)
    logical_port = random.randint(40000, 60000)

    print(f"[{thread_name}] Starting flow with bucket: {bucket_name}, file: {file_name}, port: {logical_port}")

    s3_client = make_thread_client(thread_name, logical_port)

    # Create bucket
    try:
        s3_client.create_bucket(Bucket=bucket_name)
        print(f"[{thread_name}] ✅ Created bucket: {bucket_name}")
    except Exception as e:
        print(f"[{thread_name}] ❌ Failed to create bucket: {e}")

    # List buckets
    try:
        buckets = [b["Name"] for b in s3_client.list_buckets().get("Buckets", [])]
        print(f"[{thread_name}] 📋 Listed buckets: {buckets}")
    except Exception as e:
        print(f"[{thread_name}] ❌ Failed to list buckets: {e}")

    # Upload file
    try:
        s3_client.upload_file(file_name, bucket_name, file_name)
        print(f"[{thread_name}] 📤 Uploaded file: {file_name} to {bucket_name}")
    except Exception as e:
        print(f"[{thread_name}] ❌ Failed to upload file: {e}")


if __name__ == "__main__":
    t1 = threading.Thread(target=client_flow, args=("Thread-1",))
    t2 = threading.Thread(target=client_flow, args=("Thread-2",))

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    print("✅ All threads finished.")