"""LocalPublicationAdapter（§12 第一候補: 静的JSON配信の基礎）。

publication.publish_package() が既に published/latest/ へ atomic に書き込み済み
であることを前提に、published/snapshots/ へタイムスタンプ付きコピーを残す
（履歴保持・ロールバック用）。ネットワークI/Oは行わない。
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from .base import PublicationAdapter


class LocalPublicationAdapter(PublicationAdapter):
    def __init__(self, snapshots_dir: str):
        self.snapshots_dir = Path(snapshots_dir)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def publish(self, package_dir: str) -> bool:
        src = Path(package_dir)
        if not (src / "manifest.json").exists():
            return False
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = self.snapshots_dir / stamp
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("manifest.json", "intelligence_package.json.gz"):
            p = src / name
            if p.exists():
                shutil.copy2(p, dest / name)
        return True
