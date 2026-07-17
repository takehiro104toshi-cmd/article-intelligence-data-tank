"""GitHubReleasePublicationAdapter（§12 将来移行先スタブ）。

GitHub Release Asset へのアップロードには GitHub API 呼び出し（トークン）が
必要になる。今回は有料サービス・新規外部API導入を必須にしないため未実装とし、
将来 `publish()` を実装する差し替え口だけを用意する（呼び出し側は
アダプタを差し替えるだけで移行できる）。
"""
from __future__ import annotations

from .base import PublicationAdapter


class GitHubReleasePublicationAdapter(PublicationAdapter):
    def __init__(self, repo: str = "", token_env: str = "GITHUB_TOKEN"):
        self.repo = repo
        self.token_env = token_env

    def publish(self, package_dir: str) -> bool:
        raise NotImplementedError(
            "GitHubReleasePublicationAdapter は将来実装用のスタブです。"
            "現段階では LocalPublicationAdapter / GitHubPagesPublicationAdapter を使用してください。"
        )
