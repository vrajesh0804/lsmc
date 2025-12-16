import boto3
import threading
import uuid

SIMULATOR_ENDPOINT = "http://localhost:9998"

CLIENT_ID = str(uuid.uuid4())


def worker(thread_name):
    s3 = boto3.client(
        "s3",
        endpoint_url=SIMULATOR_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

    bucket = f"sim-bucket-{thread_name.lower()}"

    print(f"\n🚀 Thread {thread_name} started")
    print(f"🧾 CLIENT ID: {CLIENT_ID}")

    try:
        s3.create_bucket(Bucket=bucket)
        print(f"✅ [{thread_name}] Created bucket → {bucket}")

        s3.list_buckets()
        print(f"📂 [{thread_name}] Listed buckets")

        s3.put_object(Bucket=bucket, Key="hello.txt", Body=b"hello simulator")
        print(f"📤 [{thread_name}] Uploaded file")

    except Exception as e:
        print(f"❌ [{thread_name}] Error → {e}")

    print(f"🧵 Thread {thread_name} finished")


threads = []

for name in ["ONE", "TWO"]:
    t = threading.Thread(target=worker, args=(name,))
    threads.append(t)
    t.start()

for t in threads:
    t.join()

print("\n✅ All client threads finished.")
