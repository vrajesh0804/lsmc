import boto3

s3 = boto3.client(
    "s3",
    endpoint_url="http://localhost:9998",
    aws_access_key_id="test",
    aws_secret_access_key="test",
)

bucket_name = "my-dynamic-bucket"

response = s3.list_objects_v2(Bucket=bucket_name)
for obj in response.get('Contents', []):
    print(f"File in bucket: {obj['Key']}")
