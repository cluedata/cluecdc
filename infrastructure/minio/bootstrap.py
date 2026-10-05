"""Create the local integration bucket without depending on an mc registry image."""

import os

import boto3
from botocore.exceptions import ClientError

client = boto3.client(
    "s3",
    endpoint_url="http://minio:9000",
    region_name="us-east-1",
    aws_access_key_id=os.environ["MINIO_ROOT_USER"],
    aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
)
try:
    client.head_bucket(Bucket="cluecdc-cdc")
except ClientError as error:
    if error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
        raise
    client.create_bucket(Bucket="cluecdc-cdc")
finally:
    client.close()
print("MinIO integration bucket ready")
