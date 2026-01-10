import threading
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

SIMULATOR_ENDPOINT = "http://localhost:9998"
CLIENT_ID = "client-A"

session = boto3.session.Session()


def make_shared_s3():
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
        params["headers"]["X-Thread-Id"] = threading.current_thread().name

    s3.meta.events.register("before-call.s3", inject_headers)
    return s3


def short_err(e: ClientError) -> str:
    http = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
    code = str(e.response.get("Error", {}).get("Code", "")).strip() or "Unknown"
    msg = str(e.response.get("Error", {}).get("Message", "")).strip() or "No message"
    return f"{code} (HTTP {http}) {msg}"


def safe_print(msg: str):
    print(msg, flush=True)


def main():
    # IMPORTANT: bucket/object state should be reset by main.py before each run.
    bucket = "mixed2t-bucket"
    key1 = "key1.txt"
    key2 = "key2.txt"

    s3 = make_shared_s3()

    def thread_1():
        try:
            safe_print("Thread-1: create_bucket")
            s3.create_bucket(Bucket=bucket)

            safe_print("Thread-1: put_object key1")
            s3.put_object(Bucket=bucket, Key=key1, Body=b"k1\n")

            safe_print("Thread-1: list_buckets")
            s3.list_buckets()
        except ClientError as e:
            safe_print(f"Thread-1: ❌ {short_err(e)}")

    def thread_2():
        try:
            safe_print("Thread-2: head_object key1 (may fail if not uploaded yet)")
            try:
                s3.head_object(Bucket=bucket, Key=key1)
                safe_print("Thread-2: head_object OK")
            except ClientError as e:
                safe_print(f"Thread-2: ❌ head_object failed: {short_err(e)}")

            safe_print("Thread-2: list_buckets")
            s3.list_buckets()

            safe_print("Thread-2: put_object key2")
            s3.put_object(Bucket=bucket, Key=key2, Body=b"k2\n")
        except ClientError as e:
            safe_print(f"Thread-2: ❌ {short_err(e)}")

    t1 = threading.Thread(target=thread_1, name="Thread-1")
    t2 = threading.Thread(target=thread_2, name="Thread-2")

    t1.start()
    t2.start()
    t1.join()
    t2.join()


if __name__ == "__main__":
    main()
