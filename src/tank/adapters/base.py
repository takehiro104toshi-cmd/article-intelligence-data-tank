"""Publication Adapter の共通インターフェース（§12）。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class PublicationAdapter(ABC):
    @abstractmethod
    def publish(self, package_dir: str) -> bool:
        """package_dir（manifest.json / intelligence_package.json.gz を含む）を配信先へ反映する。
        成功時 True。既に publication.publish_package() でローカルへ書き込み済みである前提とし、
        ここでは「配信先固有の後処理」（コピー・アップロード等）だけを行う。
        """
        raise NotImplementedError
