# simulation/client/S3/client_list_buckets_http.py
import boto3
import os
import botocore

SIMULATOR_ENDPOINT = os.environ.get("SIMULATOR_ENDPOINT", "http://localhost:9998")
SIM_ID = os.environ.get("SIMULATION_ID", "S1")

s3 = boto3.client(
    "s3",
    endpoint_url=SIMULATOR_ENDPOINT,
    aws_access_key_id="test",
    aws_secret_access_key="test",
    config=botocore.client.Config(signature_version="s3v4"),
)

def add_sim(request, **kwargs):
    try:
        request.headers['X-Simulation-ID'] = SIM_ID
    except Exception:
        pass

s3.meta.events.register('before-send', add_sim)

if __name__ == "__main__":
    print("Listing buckets...")
    resp = s3.list_buckets()
    print(resp)
