"""§25, §30-39〜42: 10,000件検索・新着0件高速終了・全件再分類しない・全件メモリ読込禁止。

厳密な時間アサーションでCIを不安定にしないよう、緩やかな上限（数秒）だけを確認する
（§25「厳密な時間テストでCIを不安定にしないでください」）。
"""
import time
from datetime import datetime, timedelta, timezone

from tank.cursor import CursorStore
from tank.index import ArticleIndex
from tank.ingestion import run_ingestion_for_source
from tank.models import SourceCursor
from tank.storage import ArticleStore
from tests.factories import make_article


def test_10000_article_search_completes_quickly(tmp_path):
    idx = ArticleIndex(str(tmp_path / "index.sqlite"))
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    batch = []
    for i in range(10_000):
        art = make_article(
            url=f"https://s{i % 50}.example/{i}", source_domain=f"s{i % 50}.example",
            category="earnings" if i % 7 else "geopolitics",
            countries=["Japan"] if i % 3 == 0 else ["US"],
            published_at=now - timedelta(hours=i % 200),
        )
        batch.append(art)
    idx.upsert_articles(batch)

    start = time.monotonic()
    result = idx.search(category="geopolitics", limit=1000)
    elapsed = time.monotonic() - start
    assert len(result) > 0
    assert elapsed < 2.0  # §25 目標: 2秒以内


def test_zero_new_articles_exits_fast(tmp_path):
    store = ArticleStore(str(tmp_path / "store"))
    index = ArticleIndex(str(tmp_path / "index.sqlite"))
    cursor_store = CursorStore(str(tmp_path / "cursors.json"))
    clusters = {}

    def _empty_fetch(source_cfg, cursor):
        return []

    start = time.monotonic()
    summary = run_ingestion_for_source(
        {"name": "src_a"}, _empty_fetch, store, index, cursor_store, clusters
    )
    elapsed = time.monotonic() - start
    assert summary == {"source": "src_a", "fetched": 0, "new": 0, "duplicates": 0}
    assert elapsed < 1.0


def test_duplicate_articles_are_not_reclassified(tmp_path):
    """既に index にある記事（重複）は classify_article が呼ばれる新規パスへ進まない。
    重複件数として計上され、分類コストを payません。"""
    store = ArticleStore(str(tmp_path / "store"))
    index = ArticleIndex(str(tmp_path / "index.sqlite"))
    cursor_store = CursorStore(str(tmp_path / "cursors.json"))
    clusters = {}

    raw = [{"title": "重複テスト記事", "url": "https://dup.example/1", "description": "説明",
           "published_at_utc": datetime.now(timezone.utc).isoformat()}]

    def _fetch(source_cfg, cursor):
        return raw

    now = datetime.now(timezone.utc)
    first = run_ingestion_for_source({"name": "src_a"}, _fetch, store, index, cursor_store, clusters, now=now)
    assert first["new"] == 1

    second = run_ingestion_for_source({"name": "src_a"}, _fetch, store, index, cursor_store, clusters, now=now)
    assert second["new"] == 0
    assert second["duplicates"] == 1


def test_iter_shards_is_generator_not_full_memory_load(tmp_path):
    store = ArticleStore(str(tmp_path / "store"))
    for day in range(3):
        store.append_articles(
            f"2026-07-{10 + day:02d}",
            [make_article(url=f"https://x.example/{day}-{i}") for i in range(5)],
        )
    gen = store.iter_shards()
    assert hasattr(gen, "__next__")  # ジェネレータであり、リストを即時生成しない
    first = next(gen)
    assert first is not None
