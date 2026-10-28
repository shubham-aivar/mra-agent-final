"""Amazon S3 storage. Encryption comes from the bucket's default (SSE-KMS, set by the CDK stack), not from this code.

put_new relies on S3 conditional writes (If-None-Match). Verified here only against the moto S3 fake.
"""
from __future__ import annotations

from botocore.exceptions import ClientError

_CONFLICT = {"PreconditionFailed", "ConditionalRequestConflict", "412"}


class AwsStorage:
    def __init__(self, bucket: str, region: str, client=None):
        if client is None:
            import boto3
            from botocore.config import Config
            # SigV4 and the regional endpoint, so presigned URLs have a predictable host (the page's security policy names it)
            client = boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
                                  config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
        self.bucket, self.s3 = bucket, client

    def put(self, key: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)

    def put_new(self, key: str, data: bytes) -> bool:
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, IfNoneMatch="*")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in _CONFLICT:
                return False
            raise

    def get(self, key: str) -> bytes:
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise FileNotFoundError(key) from e
            raise

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", [])]
        return sorted(keys)

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list(prefix)
        for i in range(0, len(keys), 1000):
            self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})
        return len(keys)

    # ---- direct browser access (so large files never pass through the API) ----------------------------------------
    def presign_post(self, key: str, max_bytes: int, expires: int = 900) -> dict:
        """A time-limited form the browser can POST one file to: this key only, at most max_bytes (S3 enforces the size)."""
        post = self.s3.generate_presigned_post(self.bucket, key, Conditions=[["content-length-range", 1, max_bytes]], ExpiresIn=expires)
        return {"url": post["url"], "fields": post["fields"], "limit": max_bytes}

    def presign_get(self, key: str, filename: str, content_type: str, expires: int = 300) -> str:
        return self.s3.generate_presigned_url(
            "get_object", ExpiresIn=expires,
            Params={"Bucket": self.bucket, "Key": key, "ResponseContentDisposition": f'attachment; filename="{filename}"',
                    "ResponseContentType": content_type})

    def size(self, key: str) -> int:
        try:
            return int(self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                raise FileNotFoundError(key) from e
            raise
