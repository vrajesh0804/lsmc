# simulation/client/dynamoDB/client_create_table.py
import boto3
import os

SIMULATOR_ENDPOINT = os.environ.get("SIMULATOR_ENDPOINT", "http://localhost:9998")
SIM_ID = os.environ.get("SIMULATION_ID", "S3")

dynamodb = boto3.client(
    "dynamodb",
    endpoint_url=SIMULATOR_ENDPOINT,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)

# inject header using before-send
def add_sim(request, **kwargs):
    try:
        request.headers['X-Simulation-ID'] = SIM_ID
    except Exception:
        pass

dynamodb.meta.events.register('before-send', add_sim)

if __name__ == "__main__":
    table_name = "TestTable"
    print("Creating DynamoDB table:", table_name)
    try:
        dynamodb.create_table(
            TableName=table_name,
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            ProvisionedThroughput={"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
        )
        print("Create table requested.")
    except Exception as e:
        print("Create table error:", e)
