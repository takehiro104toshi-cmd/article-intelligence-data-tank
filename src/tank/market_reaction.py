"""Market Reaction Store（§21）。

新着記事/イベントから市場反応の記録を開始できる構造（全過去記事へのbackfillは
今回必須ではない）。時間窓(1h/4h/1d/5d/20d) × 対象資産のスキーマだけを保持し、
値は後から（別プロセスで）埋められる想定。市場データそのものの取得は
Data Tankの責務外（Market Intelligence側が既に持つ市場データ取得と重複させない）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

from .models import MARKET_REACTION_TARGETS, MARKET_REACTION_WINDOWS, new_market_reaction_stub


class MarketReactionStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> Dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save_all(self, data: Dict[str, dict]) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".reaction-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def start_tracking(self, event_cluster_id: str) -> dict:
        """新着イベントの市場反応トラッキングを開始する（空枠を作るだけ。§21）。"""
        data = self.load_all()
        if event_cluster_id not in data:
            data[event_cluster_id] = new_market_reaction_stub()
            self.save_all(data)
        return data[event_cluster_id]

    def record_reaction(self, event_cluster_id: str, target: str, window: str, value: Optional[float]) -> None:
        if target not in MARKET_REACTION_TARGETS or window not in MARKET_REACTION_WINDOWS:
            raise ValueError(f"unknown target/window: {target}/{window}")
        data = self.load_all()
        data.setdefault(event_cluster_id, new_market_reaction_stub())
        data[event_cluster_id].setdefault(target, {w: None for w in MARKET_REACTION_WINDOWS})
        data[event_cluster_id][target][window] = value
        self.save_all(data)

    def has_any_reaction(self, event_cluster_id: str) -> bool:
        record = self.load_all().get(event_cluster_id)
        if not record:
            return False
        return any(v is not None for target in record.values() for v in target.values())

    def reaction_magnitude(self, event_cluster_id: str) -> float:
        """記録済みの反応値から単純な大きさ（0-1）を推定する（欠損はスキップ）。"""
        record = self.load_all().get(event_cluster_id)
        if not record:
            return 0.0
        values = [abs(v) for target in record.values() for v in target.values() if v is not None]
        if not values:
            return 0.0
        avg = sum(values) / len(values)
        return max(0.0, min(1.0, avg))
