from __future__ import annotations

import asyncio

from minio import Minio


class ObjectStore:
    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @classmethod
    def connect(cls, endpoint: str, access_key: str, secret_key: str, secure: bool, bucket: str) -> "ObjectStore":
        client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        return cls(client, bucket)

    def _get(self, key: str) -> bytes:
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    async def get_html(self, key: str) -> str:
        data = await asyncio.to_thread(self._get, key)
        return data.decode("utf-8", errors="replace")
