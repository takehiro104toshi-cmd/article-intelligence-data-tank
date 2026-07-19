"""ライブ取り込みパイプライン・source_config・run_stats・セキュリティのテスト
（§14 Pipeline 21-36 / Rights・Security 37-41）。ネットワーク無し・transport注入。"""
import json
from datetime import datetime, timezone

import yaml

from tank.cursor import CursorStore
from tank.index import ArticleIndex
from tank.ingestion import run_live_ingestion_all, run_live_ingestion_for_source
from tank.publication import build_package, validate_package_schema
from tank.run_stats import build_run_stats, new_run_id, save_run_stats
from tank.source_config import enabled_sources, load_sources
from tank.storage import ArticleStore

NOW = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)

FED = b"""<rss version="2.0"><channel><title>Fed</title>
 <item><title>FRB holds rates amid inflation</title><link>https://ex.gov/fed/1?utm_source=x</link>
 <description>Policy statement</description><pubDate>Wed, 15 Jul 2026 08:00:00 GMT</pubDate></item>
 <item><title>Fed chair testimony on rates</title><link>https://ex.gov/fed/2</link>
 <description>Testimony</description><pubDate>Wed, 15 Jul 2026 07:00:00 GMT</pubDate></item>
</channel></rss>"""


def _make_env(tmp_path):
    store = ArticleStore(str(tmp_path / "store"))
    index = ArticleIndex(str(tmp_path / "idx.sqlite"))
    cursors = CursorStore(str(tmp_path / "cur.json"))
    return store, index, cursors, {}


def _static_transport(body, status=200, headers=None):
    calls = {"urls": []}

    def t(url, h, to):
        calls["urls"].append(url)
        return status, headers or {"ETag": '"e1"'}, body

    return t, calls


def test_first_fetch_stores_and_classifies_and_clusters(tmp_path):
    store, index, cursors, clusters = _make_env(tmp_path)
    transport, calls = _static_transport(FED)
    src = {"name": "fed", "url": "https://ex.gov/feed", "country": "US", "trust": 0.98}
    res = run_live_ingestion_for_source(src, store, index, cursors, clusters, NOW, transport=transport)
    assert res["status"] == "success" and res["new"] == 2
    assert index.count() == 2                       # store + index 更新
    assert len(clusters) >= 1                        # event cluster 生成
    # 分類が接続されている（primary_category が付与される）
    arts = list(store.iter_shards())
    assert all(a.primary_category for a in arts)
    # article link へ二次取得していない（フィードURLのみ）＝ raw full body を取得/保存しない（§7, 37）
    assert calls["urls"] == ["https://ex.gov/feed"]


def test_second_run_is_incremental_dedup(tmp_path):
    store, index, cursors, clusters = _make_env(tmp_path)
    transport, _ = _static_transport(FED)
    src = {"name": "fed", "url": "https://ex.gov/feed", "country": "US"}
    run_live_ingestion_for_source(src, store, index, cursors, clusters, NOW, transport=transport)
    res2 = run_live_ingestion_for_source(src, store, index, cursors, {}, NOW, transport=transport)
    assert res2["new"] == 0 and res2["duplicates"] == 2
    assert index.count() == 2                        # 再保存しない


def test_cursor_updated_with_validators(tmp_path):
    store, index, cursors, clusters = _make_env(tmp_path)
    transport, _ = _static_transport(FED, headers={"ETag": '"abc"', "Last-Modified": "Wed, 15 Jul 2026 08:00:00 GMT"})
    src = {"name": "fed", "url": "https://ex.gov/feed"}
    run_live_ingestion_for_source(src, store, index, cursors, clusters, NOW, transport=transport)
    cur = cursors.get("fed")
    assert cur.etag == '"abc"' and cur.last_modified.startswith("Wed")
    assert cur.latest_published_at.startswith("2026-07-15")
    assert cur.last_http_status == 200


def test_304_unchanged_does_not_store(tmp_path):
    store, index, cursors, clusters = _make_env(tmp_path)

    def t(url, h, to):
        return 304, {"ETag": '"e1"'}, b""

    src = {"name": "fed", "url": "https://ex.gov/feed"}
    res = run_live_ingestion_for_source(src, store, index, cursors, clusters, NOW, transport=t)
    assert res["status"] == "unchanged" and res["new"] == 0
    assert index.count() == 0
    assert cursors.get("fed").last_http_status == 304


def test_failure_does_not_advance_cursor(tmp_path):
    store, index, cursors, clusters = _make_env(tmp_path)
    # まず成功して latest_published を進める
    transport, _ = _static_transport(FED)
    src = {"name": "fed", "url": "https://ex.gov/feed"}
    run_live_ingestion_for_source(src, store, index, cursors, clusters, NOW, transport=transport)
    advanced = cursors.get("fed").latest_published_at

    # 次に失敗（403）→ latest_published_at は進めない・consecutive_failures++（§8, 25）
    def t403(url, h, to):
        return 403, {}, b""

    res = run_live_ingestion_for_source(src, store, index, cursors, {}, NOW, transport=t403)
    assert res["status"] == "failed"
    cur = cursors.get("fed")
    assert cur.latest_published_at == advanced       # 位置を進めない
    assert cur.consecutive_failures == 1
    assert cur.next_retry_at != ""


def test_source_failure_isolation_continues(tmp_path):
    store, index, cursors, clusters = _make_env(tmp_path)

    def dispatch(url, h, to):
        if "good" in url:
            return 200, {"ETag": '"e"'}, FED
        return 500, {}, b""  # bad source は失敗

    sources = [
        {"name": "bad", "url": "https://ex/bad"},
        {"name": "good", "url": "https://ex/good", "country": "US"},
    ]
    results = run_live_ingestion_all(sources, store, index, cursors, clusters, NOW, retry=0, transport=dispatch)
    statuses = {r["source"]: r["status"] for r in results}
    assert statuses["bad"] == "failed"
    assert statuses["good"] == "success"             # 1ソース失敗でも他は継続（§10）
    assert index.count() == 2


def test_all_sources_failed(tmp_path):
    store, index, cursors, clusters = _make_env(tmp_path)

    def t(url, h, to):
        return 500, {}, b""

    sources = [{"name": "a", "url": "https://ex/a"}, {"name": "b", "url": "https://ex/b"}]
    results = run_live_ingestion_all(sources, store, index, cursors, clusters, NOW, retry=0, transport=t)
    assert all(r["status"] == "failed" for r in results)
    assert index.count() == 0                        # 何も保存されない


def test_unexpected_exception_in_one_source_isolated(tmp_path):
    store, index, cursors, clusters = _make_env(tmp_path)

    def t(url, h, to):
        if "boom" in url:
            raise RuntimeError("unexpected")
        return 200, {"ETag": '"e"'}, FED

    # transport例外は fetcher が failed へ変換するが、想定外例外の isolation も確認
    sources = [{"name": "boom", "url": "https://ex/boom"}, {"name": "ok", "url": "https://ex/ok", "country": "US"}]
    results = run_live_ingestion_all(sources, store, index, cursors, clusters, NOW, retry=0, transport=t)
    statuses = {r["source"]: r["status"] for r in results}
    assert statuses["ok"] == "success"


def test_index_rebuilt_from_shards_enables_dedup(tmp_path):
    # GitHub Actions のように SQLite索引が無い状態（シャードだけ）から復元 → 増分・重複排除が効く
    from tank.ingestion import rebuild_index_from_store
    store, index, cursors, clusters = _make_env(tmp_path)
    transport, _ = _static_transport(FED)
    src = {"name": "fed", "url": "https://ex.gov/feed", "country": "US"}
    run_live_ingestion_for_source(src, store, index, cursors, clusters, NOW, transport=transport)
    assert index.count() == 2

    # 索引だけ破棄（シャードは残る）→ 新しい空索引を作りシャードから再構築
    fresh_index = ArticleIndex(str(tmp_path / "fresh.sqlite"))
    assert fresh_index.count() == 0
    restored = rebuild_index_from_store(store, fresh_index)
    assert restored == 2 and fresh_index.count() == 2

    # 再構築後は同じ記事が重複として弾かれる（再保存されない）
    res = run_live_ingestion_for_source(src, store, fresh_index, cursors, {}, NOW, transport=transport)
    assert res["new"] == 0 and res["duplicates"] == 2


def test_package_built_from_real_articles_and_valid(tmp_path):
    store, index, cursors, clusters = _make_env(tmp_path)
    transport, _ = _static_transport(FED)
    src = {"name": "fed", "url": "https://ex.gov/feed", "country": "US"}
    run_live_ingestion_for_source(src, store, index, cursors, clusters, NOW, transport=transport)

    from tank.market_reaction import MarketReactionStore
    reaction = MarketReactionStore(str(tmp_path / "react.json"))
    articles = list(store.iter_shards())
    pkg = build_package(articles=articles, clusters=clusters, cursors=cursors.load_all(),
                        reaction_store=reaction, historical_matches=[],
                        tank_status={"total_articles": index.count()}, quality={}, now=NOW)
    assert validate_package_schema(pkg)
    assert len(pkg["hot_articles"]) >= 1
    assert pkg["source_health"][0]["source_name"] == "fed"


# ---------- source_config（§5） ----------

def test_load_sources_from_file_and_inline(tmp_path):
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump({"sources": [
        {"id": "fed", "name": "Fed", "url": "https://ex/fed", "enabled": True,
         "trust_score": 98, "source_class": "primary_official", "country": "US"},
        {"id": "off", "name": "Off", "url": "https://ex/off", "enabled": False},
    ]}), encoding="utf-8")
    config = {"sources_file": "sources.yaml", "sources": [
        {"name": "inline_legacy", "url": "https://ex/legacy", "trust": 0.6, "type": "rss"},
    ]}
    srcs = load_sources(config, base_dir=tmp_path)
    ids = {s["id"] for s in srcs}
    assert {"fed", "off", "inline_legacy"} <= ids
    fed = next(s for s in srcs if s["id"] == "fed")
    assert fed["trust"] == 0.98 and fed["format"] == "auto"
    legacy = next(s for s in srcs if s["id"] == "inline_legacy")
    assert legacy["trust"] == 0.6                    # 旧 trust(0-1) を引き継ぐ
    assert {s["id"] for s in enabled_sources(srcs)} == {"fed", "inline_legacy"}  # off は除外


# ---------- run_stats（§13） ----------

def test_run_stats_build_and_save_no_secrets(tmp_path):
    results = [
        {"source": "a", "status": "success", "http_status": 200, "fetched": 5, "new": 3, "duplicates": 2},
        {"source": "b", "status": "failed", "http_status": 403, "error": "http_403"},
        {"source": "c", "status": "unchanged", "http_status": 304},
    ]
    stats = build_run_stats("run1", NOW, NOW, configured_sources=3, enabled_sources=3,
                            source_results=results, total_tank_articles=3,
                            package_items=10, package_size=1234)
    assert stats["successful_sources"] == 1 and stats["failed_sources"] == 1 and stats["unchanged_sources"] == 1
    assert stats["new_unique_articles"] == 3 and stats["exact_duplicates"] == 2
    path = save_run_stats(str(tmp_path / "stats"), stats)
    assert path.exists()
    blob = json.dumps(stats).lower()
    for secret_word in ("token", "password", "authorization", "secret", "api_key"):
        assert secret_word not in blob            # Secret を統計へ出さない（§13, 40）


# ---------- Rights / Security（37-41） ----------

def test_no_credentials_in_request_headers(tmp_path):
    from tank.fetcher import fetch_feed
    rec = {}

    def t(url, h, to):
        rec["headers"] = h
        return 200, {}, FED

    fetch_feed({"url": "https://ex/feed"}, None, transport=t)
    assert "Authorization" not in rec["headers"]     # 認証情報を付けない（41）
    assert "Cookie" not in rec["headers"]


def test_private_and_restricted_body_never_in_package(tmp_path):
    from tank.market_reaction import MarketReactionStore
    from tank.private_store import PrivateArticleStore
    priv = PrivateArticleStore(str(tmp_path / "private"))
    aid = priv.save("https://paywall/1", "有料記事", "全文の本文がここに入る" * 30, source_name="Nikkei")
    view = priv.get_public_view(aid)
    reaction = MarketReactionStore(str(tmp_path / "r.json"))
    pkg = build_package(articles=[view], clusters={}, cursors={}, reaction_store=reaction,
                        historical_matches=[], tank_status={}, quality={}, now=NOW)
    blob = json.dumps(pkg, ensure_ascii=False)
    assert "全文の本文がここに入る" not in blob        # private本文は出さない（38, 39）
    assert "full_body" not in blob
