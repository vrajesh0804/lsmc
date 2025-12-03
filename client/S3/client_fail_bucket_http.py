# simulation/client/S3/client_fail_bucket_http.py
import boto3
import os
import botocore

SIMULATOR_ENDPOINT = os.environ.get("SIMULATOR_ENDPOINT", "http://localhost:9998")
SIM_ID = os.environ.get("SIMULATION_ID", "S2")

s3 = boto3.client(
    "s3",
    endpoint_url=SIMULATOR_ENDPOINT,
    aws_access_key_id="test",
    aws_secret_access_key="test",
    config=botocore.client.Config(signature_version="s3v4"),
)

def add_headers(request, **kwargs):
    try:
        request.headers['X-Simulation-ID'] = SIM_ID
        request.headers['X-Bypass-LocalStack'] = "true"
    except Exception:
        pass

s3.meta.events.register('before-send', add_headers)

def create_bucket(bucket_name):
    try:
        print("Attempt to create (bypassed):", bucket_name)
        s3.create_bucket(Bucket=bucket_name)
        print("Create request sent (bypassed).")
    except Exception as e:
        print("Create error:", e)

def upload_file(bucket_name, file_path):
    try:
        print("Uploading (normal) file:", file_path)
        # For upload we won't bypass; remove header by temporary handler
        # Unfortunately removing handlers is awkward; easiest: create a new client without bypass header.
        s3_no_bypass = boto3.client(
            "s3",
            endpoint_url=SIMULATOR_ENDPOINT,
            aws_access_key_id="test",
            aws_secret_access_key="test",
            config=botocore.client.Config(signature_version="s3v4"),
        )
        # add only simulation id
        def add_sim(request, **kwargs):
            try:
                request.headers["X-Simulation-ID"] = SIM_ID
            except Exception:
                pass
        s3_no_bypass.meta.events.register('before-send', add_sim)
        s3_no_bypass.upload_file(Filename=file_path, Bucket=bucket_name, Key=os.path.basename(file_path))
        print("Upload complete (sent to localstack).")
    except Exception as e:
        print("Upload error:", e)

if __name__ == "__main__":
    file_path = os.path.join(os.path.dirname(__file__), "..", "..", "file.txt")
    bucket = "bypass-bucket-example"
    create_bucket(bucket)
    # now upload (this one is sent to localstack)
    upload_file(bucket, file_path)
