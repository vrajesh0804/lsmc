"""
Reset LocalStack S3 buckets and DynamoDB tables.
Requires LocalStack running on http://localhost:9999
"""

import boto3
import os
import time
from botocore.exceptions import ClientError

LS_ENDPOINT = os.environ.get("LOCALSTACK_URL", "http://localhost:9999")


def reset_s3():
    s3 = boto3.client("s3", endpoint_url=LS_ENDPOINT, aws_access_key_id="test", aws_secret_access_key="test")
    try:
        resp = s3.list_buckets()
    except Exception as e:
        print("Could not connect to LocalStack S3:", e)
        return
    for b in resp.get("Buckets", []):
        bucket = b["Name"]
        print("Clearing bucket:", bucket)
        # delete objects (and versions if present)
        try:
            objs = s3.list_objects_v2(Bucket=bucket)
            for o in objs.get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=o["Key"])
        except ClientError as e:
            print("Error deleting objects:", e)
        try:
            s3.delete_bucket(Bucket=bucket)
            print("Deleted bucket", bucket)
        except ClientError as e:
            print("Error deleting bucket", bucket, e)


def reset_dynamodb():
    db = boto3.client("dynamodb", endpoint_url=LS_ENDPOINT, aws_access_key_id="test", aws_secret_access_key="test")
    try:
        tables = db.list_tables().get("TableNames", [])
    except Exception as e:
        print("Could not connect to LocalStack DynamoDB:", e)
        return
    for t in tables:
        print("Deleting table:", t)
        try:
            db.delete_table(TableName=t)
            # wait for deletion (best-effort)
            time.sleep(0.5)
        except Exception as e:
            print("Error deleting table:", t, e)


if __name__ == "__main__":
    print("Resetting LocalStack S3 and DynamoDB on", LS_ENDPOINT)
    reset_s3()
    reset_dynamodb()
    print("Done.")
