"""Article Store（§4, §30 storage系）。

保存形式: 日次シャード（JSON Lines、1行1記事）。
  data/article_store/shards/YYYY/MM/YYYY-MM-DD.jsonl

`storage_backend: "local_sharded"` の既定実装。pyarrow/duckdb 等の重い依存を
追加せず標準ライブラリのみで動く（§28 の "必要に応じてSQLite index" の代替として、
横断検索は index.py 側の SQLite で行う）。将来 Parquet へ切り替える場合も、
本モジュールのインターフェース（append_articles/iter_shards/read_shard）を保てば
呼び出し側（ingestion.py 等）は変更不要。

安全性:
  - 追記はいったん「既存内容+新規行」を一時ファイルへ書き、os.replace で
    atomic に差し替える（§30-9 atomic write）。
  - JSON解析に失敗したシャードは quarantine/ へ退避し、処理を継続する
    （§30-10 corrupted shard quarantine）。1シャードの破損が全体を止めない。
  - 全件を一度にメモリへ読み込まない設計（iter_shards はジェネレータ）。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from .models import Article


class ArticleStore:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.shards_dir = self.base_dir / "shards"
        self.quarantine_dir = self.base_dir / "quarantine"
        self.manifests_dir = self.base_dir / "manifests"
        for d in (self.shards_dir, self.quarantine_dir, self.manifests_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---------- パス解決 ----------

    def shard_path_for_date(self, date_str: str) -> Path:
        """date_str: 'YYYY-MM-DD'（JST基準の公開日）。"""
        y, m, _ = date_str.split("-")
        d = self.shards_dir / y / m
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{date_str}.jsonl"

    def all_shard_paths(self) -> List[Path]:
        return sorted(self.shards_dir.glob("*/*/*.jsonl"))

    # ---------- 書き込み（atomic・append-oriented） ----------

    def append_articles(self, date_str: str, articles: List[Article]) -> int:
        """指定日のシャードへ記事を追記する。既存内容を読み直し、新規分と結合してから
        一時ファイル経由で atomic に差し替える（§30-5 daily shard save, §30-9 atomic write）。
        """
        if not articles:
            return 0
        path = self.shard_path_for_date(date_str)
        existing_lines: List[str] = []
        if path.exists():
            existing_lines = path.read_text(encoding="utf-8").splitlines()
        new_lines = [json.dumps(a.to_dict(), ensure_ascii=False) for a in articles]
        all_lines = existing_lines + new_lines
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".shard-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(all_lines) + ("\n" if all_lines else ""))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return len(articles)

    # ---------- 読み込み（quarantine対応・ジェネレータ） ----------

    def read_shard(self, path: Path) -> List[Article]:
        """1シャードを読む。JSON解析に失敗した行を含む壊れたシャードは quarantine/ へ
        退避して空リストを返す（処理を継続させるため例外は投げない）。
        """
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        articles: List[Article] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                articles.append(Article.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                self._quarantine(path)
                return []
        return articles

    def _quarantine(self, path: Path) -> None:
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        dest = self.quarantine_dir / f"{path.stem}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.corrupt"
        try:
            shutil.move(str(path), str(dest))
        except OSError:
            pass

    def iter_shards(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Iterator[Article]:
        """全シャードを横断してジェネレータで記事を返す（§30-6, §30-42 全件メモリ読込禁止）。
        date_from/date_to（'YYYY-MM-DD'）を指定すると対象シャードを絞り込む。
        """
        for path in self.all_shard_paths():
            date_str = path.stem
            if date_from and date_str < date_from:
                continue
            if date_to and date_str > date_to:
                continue
            for article in self.read_shard(path):
                yield article

    # ---------- manifest ----------

    def build_manifest(self) -> dict:
        shard_paths = self.all_shard_paths()
        total = 0
        oldest = None
        newest = None
        for p in shard_paths:
            count = sum(1 for _ in self.read_shard(p))
            total += count
            date_str = p.stem
            if oldest is None or date_str < oldest:
                oldest = date_str
            if newest is None or date_str > newest:
                newest = date_str
        manifest = {
            "shard_count": len(shard_paths),
            "total_articles": total,
            "oldest_date": oldest,
            "newest_date": newest,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        return manifest

    def write_manifest(self) -> Path:
        manifest = self.build_manifest()
        path = self.manifests_dir / "article_store_manifest.json"
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".manifest-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return path
