import boto3

dynamodb = boto3.client(
    "dynamodb",
    endpoint_url="http://localhost:9998",
    aws_access_key_id="test",
    aws_secret_access_key="test",
    region_name="us-east-1"
)

print("➡️ Listing all tables")
try:
    response = dynamodb.list_tables()
    print(f"📝 Tables: {response.get('TableNames', [])}")
except Exception as e:
    print(f"❌ Failed to list tables: {e}")
