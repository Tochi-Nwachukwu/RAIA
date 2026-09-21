"""Where audio lives: S3 or Cloudflare R2 when configured, otherwise runs/ served by the API.
Audio never goes in Mongo; Mongo holds the key."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.settings import get_settings


class LocalBlobs:
    """Files stay in runs/; the API serves them at /audio/."""

    def __init__(self):
        self.settings = get_settings()

    async def put(self, path: Path) -> str:
        key = path.resolve().relative_to(self.settings.runs_dir.resolve()).as_posix()
        return f"{self.settings.public_base_url.rstrip('/')}/audio/{key}"


class S3Blobs:
    """S3, or Cloudflare R2 through its S3-compatible endpoint (s3_endpoint_url)."""

    def __init__(self):
        import boto3

        self.settings = get_settings()
        self.client = boto3.client(
            "s3", endpoint_url=self.settings.s3_endpoint_url,
            aws_access_key_id=self.settings.s3_access_key_id, aws_secret_access_key=self.settings.s3_secret_access_key,
        )

    async def put(self, path: Path) -> str:
        key = path.resolve().relative_to(self.settings.runs_dir.resolve()).as_posix()
        await asyncio.to_thread(self.client.upload_file, str(path), self.settings.s3_bucket, key,
                                ExtraArgs={"ContentType": "audio/mpeg", "CacheControl": "public, max-age=31536000"})
        return f"{self.settings.s3_public_base_url.rstrip('/')}/{key}"


def blob_store() -> LocalBlobs | S3Blobs:
    return S3Blobs() if get_settings().blob_backend == "s3" else LocalBlobs()
