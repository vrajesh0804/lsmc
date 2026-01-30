"""
Reset LocalStack S3 buckets and DynamoDB tables.
Requires LocalStack running on http://localhost:9999

This version is robust:
- Retries transient failures
- Waits until resources are actually deleted (best-effort polling)
"""

import os
import time
import boto3
from botocore.exceptions import ClientError

LS_ENDPOINT = os.environ.get("LOCALSTACK_URL", "http://localhost:9999")

AWS_KEY = "test"
AWS_SECRET = "test"

# Tunables (safe defaults)
POLL_INTERVAL_S = float(os.environ.get("LS_RESET_POLL_INTERVAL", "0.25"))
MAX_WAIT_S = float(os.environ.get("LS_RESET_MAX_WAIT", "10.0"))
MAX_RETRIES = int(os.environ.get("LS_RESET_RETRIES", "5"))


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=LS_ENDPOINT,
        aws_access_key_id=AWS_KEY,
        aws_secret_access_key=AWS_SECRET,
    )


def _ddb():
    return boto3.client(
        "dynamodb",
        endpoint_url=LS_ENDPOINT,
        aws_access_key_id=AWS_KEY,
        aws_secret_access_key=AWS_SECRET,
    )


def _retry(fn, *, what: str):
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(POLL_INTERVAL_S * attempt)
    raise RuntimeError(f"[reset_localstack] Failed {what} after {MAX_RETRIES} retries: {last}") from last


def _bucket_exists(s3, bucket: str) -> bool:
    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", "")).lower()
        http = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        # LocalStack sometimes returns 404, sometimes NoSuchBucket-ish codes
        if http == 404 or "nosuchbucket" in code or "notfound" in code:
            return False
        # if something else, assume it exists / transient; caller will retry
        return True
    except Exception:
        return True


def _delete_all_objects_best_effort(s3, bucket: str) -> None:
    # For typical LocalStack usage, versioning isn’t enabled.
    # We do a best-effort delete of listed objects.
    try:
        cont_token = None
        while True:
            kwargs = {"Bucket": bucket}
            if cont_token:
                kwargs["ContinuationToken"] = cont_token
            resp = s3.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                try:
                    s3.delete_object(Bucket=bucket, Key=key)
                except ClientError:
                    pass
            if resp.get("IsTruncated"):
                cont_token = resp.get("NextContinuationToken")
            else:
                break
    except ClientError:
        pass


def reset_s3() -> None:
    s3 = _s3()

    try:
        buckets = _retry(lambda: s3.list_buckets().get("Buckets", []), what="list_buckets")
    except Exception as e:
        print("[reset_localstack] Could not connect to LocalStack S3:", e, flush=True)
        return

    for b in buckets:
        bucket = b.get("Name")
        if not bucket:
            continue

        print("[reset_localstack] Clearing bucket:", bucket, flush=True)

        _retry(lambda: _delete_all_objects_best_effort(s3, bucket), what=f"delete objects in {bucket}")

        # Delete bucket (retry a few times)
        def _del_bucket():
            try:
                s3.delete_bucket(Bucket=bucket)
            except ClientError:
                # may fail transiently if objects still being cleared
                raise

        try:
            _retry(_del_bucket, what=f"delete_bucket {bucket}")
        except Exception as e:
            print("[reset_localstack] Error deleting bucket", bucket, e, flush=True)

        # Wait until it’s really gone (best-effort)
        t0 = time.time()
        while time.time() - t0 < MAX_WAIT_S:
            if not _bucket_exists(s3, bucket):
                break
            time.sleep(POLL_INTERVAL_S)

        if _bucket_exists(s3, bucket):
            print("[reset_localstack] WARNING: bucket still appears to exist after wait:", bucket, flush=True)
        else:
            print("[reset_localstack] Deleted bucket:", bucket, flush=True)


def reset_dynamodb() -> None:
    db = _ddb()

    try:
        tables = _retry(lambda: db.list_tables().get("TableNames", []), what="list_tables")
    except Exception as e:
        print("[reset_localstack] Could not connect to LocalStack DynamoDB:", e, flush=True)
        return

    for t in tables:
        print("[reset_localstack] Deleting table:", t, flush=True)

        def _del_table():
            db.delete_table(TableName=t)

        try:
            _retry(_del_table, what=f"delete_table {t}")
        except Exception as e:
            print("[reset_localstack] Error deleting table:", t, e, flush=True)

        # Wait until it’s really gone (best-effort)
        t0 = time.time()
        while time.time() - t0 < MAX_WAIT_S:
            cur = _retry(lambda: db.list_tables().get("TableNames", []), what="list_tables (poll)")
            if t not in cur:
                break
            time.sleep(POLL_INTERVAL_S)

        cur = _retry(lambda: db.list_tables().get("TableNames", []), what="list_tables (final)")
        if t in cur:
            print("[reset_localstack] WARNING: table still appears to exist after wait:", t, flush=True)
        else:
            print("[reset_localstack] Deleted table:", t, flush=True)


if __name__ == "__main__":
    print("[reset_localstack] Resetting LocalStack S3 and DynamoDB on", LS_ENDPOINT, flush=True)
    reset_s3()
    reset_dynamodb()
    print("[reset_localstack] Done.", flush=True)
