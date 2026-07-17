"""ソース別 cursor（§10）。増分取得のための状態を atomic に読み書きする。"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from .models import SourceCursor


class CursorStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> Dict[str, SourceCursor]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return {name: SourceCursor.from_dict(v) for name, v in data.items()}

    def get(self, source_name: str) -> SourceCursor:
        cursors = self.load_all()
        return cursors.get(source_name, SourceCursor(source_name=source_name))

    def save_all(self, cursors: Dict[str, SourceCursor]) -> None:
        payload = {name: c.to_dict() for name, c in cursors.items()}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".cursor-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def update(self, cursor: SourceCursor) -> None:
        cursors = self.load_all()
        cursors[cursor.source_name] = cursor
        self.save_all(cursors)


def fetch_from_datetime(cursor: SourceCursor, overlap_hours: int = 48) -> Optional[datetime]:
    """重複取得ウィンドウ（§10 overlap_hours）を考慮した「ここから取得すべき」時刻を返す。

    latest_published_at が無ければ None（＝全件/既定ウィンドウで取得）。
    """
    if not cursor.latest_published_at:
        return None
    try:
        latest = datetime.fromisoformat(cursor.latest_published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return latest - timedelta(hours=overlap_hours)


def should_retry_now(cursor: SourceCursor, now: datetime) -> bool:
    """consecutive_failures がある場合、next_retry_at を過ぎるまで再取得しない。"""
    if not cursor.next_retry_at:
        return True
    try:
        next_retry = datetime.fromisoformat(cursor.next_retry_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return now >= next_retry
