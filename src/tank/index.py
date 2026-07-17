"""横断検索インデックス（§4 の DuckDB 案の代替として SQLite を使用）。

config.yaml の storage_backend が "local_sharded" である限り、重い依存
(pyarrow/duckdb) を追加せず標準ライブラリ sqlite3 のみで動く。将来 DuckDB へ
切り替える場合も、本モジュールの関数シグネチャ（upsert_articles/search/
get_by_canonical_hash 等）を保てば呼び出し側は変更不要（アダプタ差し替え）。

日付・カテゴリ・国・地政学エンティティ・企業・資源・ソース信頼度・
retrieval score・event_cluster での検索に対応する（§30-17〜24）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional

from .models import Article

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    article_id TEXT PRIMARY KEY,
    canonical_hash TEXT,
    content_hash TEXT,
    title_hash TEXT,
    published_at_utc TEXT,
    source_domain TEXT,
    source_trust REAL,
    primary_category TEXT,
    countries TEXT,
    companies TEXT,
    commodities TEXT,
    geopolitical_entities TEXT,
    themes TEXT,
    event_cluster_id TEXT,
    importance_score REAL,
    market_impact_score REAL,
    shard_date TEXT,
    duplicate_group_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_published ON articles(published_at_utc);
CREATE INDEX IF NOT EXISTS idx_category ON articles(primary_category);
CREATE INDEX IF NOT EXISTS idx_cluster ON articles(event_cluster_id);
CREATE INDEX IF NOT EXISTS idx_canonical ON articles(canonical_hash);
CREATE INDEX IF NOT EXISTS idx_title_hash ON articles(title_hash);
"""


class ArticleIndex:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_articles(self, articles: Iterable[Article], shard_date: str = "") -> int:
        rows = [
            (
                a.article_id, a.canonical_hash, a.content_hash, a.title_hash,
                a.published_at_utc, a.source_domain, a.source_trust, a.primary_category,
                json.dumps(a.countries, ensure_ascii=False), json.dumps(a.companies, ensure_ascii=False),
                json.dumps(a.commodities, ensure_ascii=False), json.dumps(a.geopolitical_entities, ensure_ascii=False),
                json.dumps(a.themes, ensure_ascii=False), a.event_cluster_id,
                a.importance_score, a.market_impact_score, shard_date, a.duplicate_group_id,
            )
            for a in articles
        ]
        if not rows:
            return 0
        self._conn.executemany(
            """INSERT OR REPLACE INTO articles
               (article_id, canonical_hash, content_hash, title_hash, published_at_utc, source_domain,
                source_trust, primary_category, countries, companies, commodities, geopolitical_entities,
                themes, event_cluster_id, importance_score, market_impact_score, shard_date, duplicate_group_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def canonical_hash_map(self) -> dict:
        cur = self._conn.execute("SELECT canonical_hash, duplicate_group_id FROM articles WHERE canonical_hash != ''")
        return {row[0]: (row[1] or row[0]) for row in cur.fetchall()}

    def content_hash_map(self) -> dict:
        cur = self._conn.execute("SELECT content_hash, duplicate_group_id FROM articles WHERE content_hash != ''")
        return {row[0]: (row[1] or row[0]) for row in cur.fetchall()}

    def title_hash_map(self) -> dict:
        cur = self._conn.execute("SELECT title_hash, duplicate_group_id FROM articles WHERE title_hash != ''")
        return {row[0]: (row[1] or row[0]) for row in cur.fetchall()}

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    def count_since(self, iso_utc_from: str) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM articles WHERE published_at_utc >= ?", (iso_utc_from,)
        ).fetchone()[0]

    def source_domain_count(self) -> int:
        return self._conn.execute("SELECT COUNT(DISTINCT source_domain) FROM articles").fetchone()[0]

    def latest_published_at(self) -> Optional[str]:
        row = self._conn.execute("SELECT MAX(published_at_utc) FROM articles").fetchone()
        return row[0] if row else None

    def search(
        self,
        category: Optional[str] = None,
        country: Optional[str] = None,
        company: Optional[str] = None,
        commodity: Optional[str] = None,
        geopolitical_entity: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_source_trust: Optional[float] = None,
        limit: int = 1000,
    ) -> List[str]:
        """条件検索し article_id のリストを返す（§30-17〜23）。全件ロードせず SQL 側で絞り込む。"""
        clauses = []
        params: list = []
        if category:
            clauses.append("primary_category = ?")
            params.append(category)
        if country:
            clauses.append("countries LIKE ?")
            params.append(f"%{country}%")
        if company:
            clauses.append("companies LIKE ?")
            params.append(f"%{company}%")
        if commodity:
            clauses.append("commodities LIKE ?")
            params.append(f"%{commodity}%")
        if geopolitical_entity:
            clauses.append("geopolitical_entities LIKE ?")
            params.append(f"%{geopolitical_entity}%")
        if date_from:
            clauses.append("published_at_utc >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("published_at_utc <= ?")
            params.append(date_to)
        if min_source_trust is not None:
            clauses.append("source_trust >= ?")
            params.append(min_source_trust)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT article_id FROM articles {where} ORDER BY published_at_utc DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(sql, params)
        return [row[0] for row in cur.fetchall()]
