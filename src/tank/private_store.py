"""Private Article Store（§27）。

将来ユーザーが貼り付ける有料記事（例: 日経新聞）の原文を保存する専用領域。
原文は publication.py から一切参照されない（公開Packageのスキーマは
allowlistフィールドのみを組み立てるため、構造的にも本文を含められない）。

今回スマホ入力UIは対象外だが、Storage Adapter と Schema だけを用意する（§27）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from .dedup import article_id_from_canonical
from .models import Article


class PrivateArticleStore:
    """rights_classification="private" の記事本文を、公開領域と完全に分離して保存する。"""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, canonical_url: str, title: str, full_body: str, source_name: str = "") -> str:
        article_id = article_id_from_canonical(canonical_url or title)
        record = {
            "article_id": article_id,
            "canonical_url": canonical_url,
            "title_original": title,
            "full_body": full_body,   # ここにのみ保持。公開Packageへは絶対に渡さない。
            "source_name": source_name,
            "rights_classification": "private",
        }
        path = self.base_dir / f"{article_id}.json"
        fd, tmp = tempfile.mkstemp(dir=str(self.base_dir), prefix=".private-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return article_id

    def get_public_view(self, article_id: str) -> Optional[Article]:
        """公開可能な範囲（本文を含まない）だけを Article として返す。"""
        path = self.base_dir / f"{article_id}.json"
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        return Article(
            article_id=record["article_id"],
            canonical_url=record.get("canonical_url", ""),
            title_original=record.get("title_original", ""),
            source_name=record.get("source_name", ""),
            body_storage_type="private",
            body_available=False,
            rights_classification="private",
            public_excerpt="",  # 本文由来の抜粋も出さない（ユーザーが別途タグ付けするまでは空）
        )
