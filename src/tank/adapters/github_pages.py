"""GitHubPagesPublicationAdapter（§12 将来移行先スタブ）。

実配信は独立プロジェクトのGitHub Actions workflow（.github/workflows/
article-tank-update.yml）が published/ 配下を commit/push し、GitHub Pages の
ビルド設定（Pages: Deploy from a branch もしくは Actions）に任せる。
本アダプタはローカル検証用に「Pagesが読みにいくディレクトリへのコピー」だけを行う
薄い実装で、実際のPages設定・カスタムドメイン設定は利用者側のリポジトリ設定が必要。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .base import PublicationAdapter


class GitHubPagesPublicationAdapter(PublicationAdapter):
    def __init__(self, pages_dir: str):
        self.pages_dir = Path(pages_dir)
        self.pages_dir.mkdir(parents=True, exist_ok=True)

    def publish(self, package_dir: str) -> bool:
        src = Path(package_dir)
        if not (src / "manifest.json").exists():
            return False
        dest = self.pages_dir / "latest"
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("manifest.json", "intelligence_package.json.gz"):
            p = src / name
            if p.exists():
                shutil.copy2(p, dest / name)
        return True
