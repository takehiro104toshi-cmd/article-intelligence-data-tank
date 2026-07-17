"""ingestion.py の end-to-end（フェイクfetch_fn使用・ネットワークなし）。"""
from datetime import datetime, timezone

from tank.cursor import CursorStore
from tank.index import ArticleIndex
from tank.ingestion import run_ingestion_all
from tank.storage import ArticleStore


def test_end_to_end_ingestion_dedup_and_cluster(tmp_path):
    store = ArticleStore(str(tmp_path / "store"))
    index = ArticleIndex(str(tmp_path / "index.sqlite"))
    cursor_store = CursorStore(str(tmp_path / "cursors.json"))
    clusters = {}
    now = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)

    def fetch_a(source_cfg, cursor):
        return [
            {"title": "台湾を巡る米中摩擦が再燃", "url": "https://a.example/1?utm_source=rss",
             "description": "台湾情勢が緊迫化しています。", "published_at_utc": now.isoformat()},
        ]

    def fetch_b(source_cfg, cursor):
        return [
            {"title": "米中摩擦を巡り台湾情勢が緊迫", "url": "https://b.example/2",
             "description": "別ソースからの報道。", "published_at_utc": now.isoformat()},
            {"title": "決算発表シーズンが到来", "url": "https://b.example/3",
             "description": "国内企業の決算発表が本格化。", "published_at_utc": now.isoformat()},
        ]

    sources = [
        {"name": "src_a", "trust": 0.8, "type": "rss"},
        {"name": "src_b", "trust": 0.6, "type": "rss"},
    ]

    def fetch_dispatch(source_cfg, cursor):
        return fetch_a(source_cfg, cursor) if source_cfg["name"] == "src_a" else fetch_b(source_cfg, cursor)

    summaries = run_ingestion_all(sources, fetch_dispatch, store, index, cursor_store, clusters, now=now)
    total_new = sum(s["new"] for s in summaries)
    assert total_new == 3
    assert index.count() == 3

    # 同じ内容を再取得しても新規0件（重複除去が効く）
    summaries2 = run_ingestion_all(sources, fetch_dispatch, store, index, cursor_store, clusters, now=now)
    assert sum(s["new"] for s in summaries2) == 0
    assert sum(s["duplicates"] for s in summaries2) == 3
