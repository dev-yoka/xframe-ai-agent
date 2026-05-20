"""S3-compatible and local attachment blob storage."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from xframe_agent.settings import Settings


@dataclass(frozen=True)
class StoredBlob:
    bucket: str
    key: str


class BlobStorage(Protocol):
    """Storage API used by the attachment router and scan worker."""

    async def put_bytes(self, *, key: str, data: bytes, content_type: str) -> StoredBlob:
        """Store one immutable blob."""

    async def get_bytes(self, *, key: str) -> bytes:
        """Read one blob for asynchronous scan or extraction jobs."""

    async def presigned_get_url(self, *, key: str, expires_seconds: int = 300) -> str | None:
        """Return a short-lived download URL when supported."""


class LocalBlobStorage:
    """Filesystem storage used by tests and isolated development."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def put_bytes(self, *, key: str, data: bytes, content_type: str) -> StoredBlob:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredBlob(bucket="local", key=key)

    async def get_bytes(self, *, key: str) -> bytes:
        return (self._root / key).read_bytes()

    async def presigned_get_url(self, *, key: str, expires_seconds: int = 300) -> str | None:
        return None


class S3BlobStorage:
    """S3-compatible storage backed by boto3, suitable for MinIO in dev."""

    def __init__(self, settings: Settings) -> None:
        boto3 = importlib.import_module("boto3")
        self._bucket = settings.s3_bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )

    async def put_bytes(self, *, key: str, data: bytes, content_type: str) -> StoredBlob:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return StoredBlob(bucket=self._bucket, key=key)

    async def get_bytes(self, *, key: str) -> bytes:
        response: Any = await asyncio.to_thread(
            self._client.get_object,
            Bucket=self._bucket,
            Key=key,
        )
        body = response["Body"]
        return await asyncio.to_thread(body.read)

    async def presigned_get_url(self, *, key: str, expires_seconds: int = 300) -> str | None:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )


def storage_from_settings(settings: Settings) -> BlobStorage:
    """Build the configured attachment storage backend."""

    if settings.attachment_storage_backend == "local":
        return LocalBlobStorage(Path(settings.attachment_local_storage_path))
    return S3BlobStorage(settings)
