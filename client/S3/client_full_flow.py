import boto3
import json
import os

SIMULATOR_ENDPOINT = "http://localhost:9998"

# ----------------------------------------------------
# Make sure file.txt exists
# ----------------------------------------------------
FILE_TO_UPLOAD = "file.txt"

if not os.path.isfile(FILE_TO_UPLOAD):
    with open(FILE_TO_UPLOAD, "w") as f:
        f.write("This is a test file for upload.\n")

# ----------------------------------------------------
# Create S3 client pointing to simulator
# ----------------------------------------------------
s3 = boto3.client(
    "s3",
    endpoint_url=SIMULATOR_ENDPOINT,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)


def print_section(title):
    print("\n" + "━" * 70)
    print(title)
    print("━" * 70 + "\n")


# ----------------------------------------------------
# Create Bucket
# ----------------------------------------------------
def create_bucket(bucket_name):
    try:
        response = s3.create_bucket(Bucket=bucket_name)
        status = response["ResponseMetadata"]["HTTPStatusCode"]
        location = response.get("Location", "/" + bucket_name)

        print("🌟 Bucket Created Successfully!")
        print(f"📦 Bucket Name: {bucket_name}")
        return bucket_name

    except Exception as e:
        print_section("❌ CLIENT ERROR")
        print(f"🚫 Failed to create bucket '{bucket_name}': {str(e)}\n")
        return None


# ----------------------------------------------------
# List Buckets
# ----------------------------------------------------
def list_buckets():
    try:
        response = s3.list_buckets()
        status = response["ResponseMetadata"]["HTTPStatusCode"]
        buckets = [b["Name"] for b in response.get("Buckets", [])]

        if buckets:
            print(f"🌟 Buckets Available: {', '.join(buckets)}")
        else:
            print("🌟 No buckets found.")

        return buckets

    except Exception as e:
        print_section("❌ CLIENT ERROR")
        print(f"🚫 Failed to list buckets: {str(e)}\n")
        return []


# ----------------------------------------------------
# Upload File
# ----------------------------------------------------
def upload_file(bucket_name, file_name):
    try:
        # boto3 upload_file() does not return metadata
        s3.upload_file(Filename=file_name, Bucket=bucket_name, Key=file_name)

        print("🌟 File Uploaded Successfully!")
        print(f"📦 Bucket: {bucket_name}")
        print(f"📄 File: {file_name}")

    except Exception as e:
        print_section("❌ CLIENT ERROR")
        print(f"🚫 Failed to upload '{file_name}' to '{bucket_name}': {str(e)}\n")


# ======================
# Run Full Flow
# ======================
bucket_name = "my-dynamic-bucket"

# 1️⃣ Create bucket
created_bucket = create_bucket(bucket_name)

# 2️⃣ List buckets
if created_bucket:
    list_buckets()

# 3️⃣ Upload file
if created_bucket:
    upload_file(created_bucket, FILE_TO_UPLOAD)
