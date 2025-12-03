# simulation/client/dynamoDB/client_create_list_upload.py
# Example: create table, list tables, put item
import boto3
import os
import json
SIMULATOR_ENDPOINT = os.environ.get("SIMULATOR_ENDPOINT", "http://localhost:9998")
SIM_ID = os.environ.get("SIMULATION_ID", "S3")

db = boto3.client("dynamodb", endpoint_url=SIMULATOR_ENDPOINT, aws_access_key_id="test", aws_secret_access_key="test")

def add_sim(request, **kwargs):
    try:
        request.headers['X-Simulation-ID'] = SIM_ID
    except Exception:
        pass

db.meta.events.register('before-send', add_sim)

if __name__ == "__main__":
    try:
        db.create_table(
            TableName="FlowTable",
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            ProvisionedThroughput={"ReadCapacityUnits": 1, "WriteCapacityUnits": 1}
        )
    except Exception as e:
        print("Create table (may already exist):", e)
    print("Tables:", db.list_tables())
    try:
        db.put_item(TableName="FlowTable", Item={"id": {"S": "1"}, "value": {"S": "hello"}})
    except Exception as e:
        print("Put item error (maybe table not active yet):", e)
