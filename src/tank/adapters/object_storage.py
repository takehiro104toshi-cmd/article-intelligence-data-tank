"""FutureObjectStoragePublicationAdapter（§12 将来移行先スタブ: S3互換/Cloudflare R2等）。

有料サービス導入を今回必須にしないため未実装。将来、boto3等のクライアントを
注入して `publish()` を実装するだけで済むよう、コンストラクタで endpoint/bucket/
認証情報を受け取る形にしている。
"""
from __future__ import annotations

from typing import Optional

from .base import PublicationAdapter


class FutureObjectStoragePublicationAdapter(PublicationAdapter):
    def __init__(self, endpoint_url: str = "", bucket: str = "", credentials: Optional[dict] = None):
        self.endpoint_url = endpoint_url
        self.bucket = bucket
        self.credentials = credentials or {}

    def publish(self, package_dir: str) -> bool:
        raise NotImplementedError(
            "FutureObjectStoragePublicationAdapter は将来実装用のスタブです（S3互換/Cloudflare R2等）。"
        )
