"""§30-5/6/7/9/10: 日次シャード保存・複数シャード検索・manifest生成・atomic write・
corrupted shard quarantine。"""
from datetime import datetime, timezone

from tank.storage import ArticleStore
from tests.factories import make_article


def test_daily_shard_save_creates_expected_path(tmp_path):
    store = ArticleStore(str(tmp_path))
    art = make_article(url="https://example.com/1")
    store.append_articles("2026-07-15", [art])
    path = store.shard_path_for_date("2026-07-15")
    assert path.exists()
    assert path.parent.name == "07" and path.parent.parent.name == "2026"


def test_append_is_additive_not_overwriting(tmp_path):
    store = ArticleStore(str(tmp_path))
    store.append_articles("2026-07-15", [make_article(url="https://example.com/1")])
    store.append_articles("2026-07-15", [make_article(url="https://example.com/2")])
    articles = store.read_shard(store.shard_path_for_date("2026-07-15"))
    assert len(articles) == 2


def test_multi_shard_search_across_dates(tmp_path):
    store = ArticleStore(str(tmp_path))
    store.append_articles("2026-07-14", [make_article(url="https://example.com/a")])
    store.append_articles("2026-07-15", [make_article(url="https://example.com/b")])
    all_articles = list(store.iter_shards())
    assert len(all_articles) == 2
    only_15 = list(store.iter_shards(date_from="2026-07-15"))
    assert len(only_15) == 1


def test_manifest_generation(tmp_path):
    store = ArticleStore(str(tmp_path))
    store.append_articles("2026-07-15", [make_article(url="https://example.com/1")])
    manifest = store.build_manifest()
    assert manifest["total_articles"] == 1
    assert manifest["shard_count"] == 1
    path = store.write_manifest()
    assert path.exists()


def test_atomic_write_leaves_no_temp_files(tmp_path):
    store = ArticleStore(str(tmp_path))
    store.append_articles("2026-07-15", [make_article()])
    leftover_tmp = list((tmp_path / "shards").glob("**/*.tmp"))
    assert leftover_tmp == []


def test_corrupted_shard_is_quarantined_and_pipeline_continues(tmp_path):
    store = ArticleStore(str(tmp_path))
    store.append_articles("2026-07-15", [make_article()])
    store.append_articles("2026-07-16", [make_article(url="https://example.com/ok")])

    # 2026-07-15 のシャードを意図的に壊す
    bad_path = store.shard_path_for_date("2026-07-15")
    bad_path.write_text("{not valid json", encoding="utf-8")

    all_articles = list(store.iter_shards())
    # 壊れたシャードは quarantine され、正常な 07-16 分だけ読める（例外は投げない）
    assert len(all_articles) == 1
    quarantined = list(store.quarantine_dir.glob("*.corrupt"))
    assert len(quarantined) == 1
    assert not bad_path.exists()
