"""Phase 3 Batch 1 テスト（§17）: 並列取得・Source検証・Coverage・Dedup・開示分類。

ネットワークは使わず、transport を注入して決定的に検証する。
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from tank.cursor import CursorStore
from tank.index import ArticleIndex
from tank.ingestion import run_live_ingestion_all
from tank.source_portfolio import (
    classify_disclosure, coverage_gaps, coverage_metrics, validate_sources,
)
from tank.storage import ArticleStore
from tank.url_normalize import normalize_url

NOW = datetime(2026, 7, 21, 3, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent


# ---------- Source config 検証（§17-1〜9, §21） ----------

def test_actual_sources_yaml_valid():
    d = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    errors = validate_sources(d.get("sources", []))
    assert errors == [], f"sources.yaml validation errors: {errors}"


def test_validate_detects_duplicate_id_and_url():
    srcs = [
        {"id": "a", "name": "A", "url": "https://x/1", "enabled": True,
         "source_class": "major_news", "country": "US", "region": "North America", "language": "en"},
        {"id": "a", "name": "B", "url": "https://x/1", "enabled": False,
         "source_class": "major_news", "country": "US", "region": "North America", "language": "en"},
    ]
    errs = validate_sources(srcs)
    assert any("duplicate source id" in e for e in errs)
    assert any("duplicate source url" in e for e in errs)


def test_validate_missing_field_and_bad_values():
    srcs = [{"id": "x", "name": "X", "url": "https://x", "enabled": True,
             "source_class": "not_a_class", "country": "US", "region": "NA", "language": "en",
             "tier": 9, "trust_score": 250}]
    errs = validate_sources(srcs)
    assert any("invalid source_class" in e for e in errs)
    assert any("invalid tier" in e for e in errs)
    assert any("trust_score out of range" in e for e in errs)


_VERIFIED_ENABLE_STATUSES = {"verified_healthy", "verified_unchanged"}
_UNVERIFIED_STATUSES = {"pending", "unreachable", "forbidden_403", "verified_but_unstable",
                        "not_feed", "malformed", "requires_auth", "deprecated", "duplicate_source"}


def test_no_source_enabled_without_verification():
    """§6: 到達性が確認できていないソースを有効化しない（推測で有効化しない）。

    verify_status を明示しない既存ソース（安定運用中の既定ソース）は対象外とし、
    verify_status フィールドを持つソースについてのみ、有効化には検証済みステータスを要求する。
    """
    d = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    for s in d["sources"]:
        vs = s.get("verify_status")
        if vs is None:
            continue
        if s["enabled"]:
            assert vs in _VERIFIED_ENABLE_STATUSES, f"{s['id']}: enabled=True だが verify_status={vs}"
        else:
            assert vs in _UNVERIFIED_STATUSES, f"{s['id']}: 未知の verify_status={vs}"


def test_batch1_candidates_all_resolved_no_pending():
    """Phase 3 Batch 1で追加した候補は、到達性確認（verify_candidates）を経て
    全て pending から解消済みであること（12件到達可・残りは404/403として記録）。"""
    d = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    pending = [s for s in d["sources"] if s.get("verify_status") == "pending"]
    assert pending == [], f"未検証のまま残っている候補があります: {[s['id'] for s in pending]}"


# ---------- Coverage 指標（§17-38〜43） ----------

def test_coverage_metrics_and_gaps():
    srcs = [
        {"enabled": True, "source_class": "primary_official", "country": "JP", "region": "Asia", "language": "ja", "primary_category": "monetary_policy"},
        {"enabled": True, "source_class": "regulator", "country": "US", "region": "North America", "language": "en", "primary_category": "regulation"},
        {"enabled": True, "source_class": "major_news", "country": "US", "region": "North America", "language": "en", "primary_category": "geopolitics"},
        {"enabled": False, "source_class": "major_news", "country": "GB", "region": "Europe", "language": "en", "primary_category": "earnings"},
    ]
    m = coverage_metrics(srcs)
    assert m["enabled_total"] == 3
    assert m["tier1_share"] == round(2 / 3, 4)        # 2 Tier1 of 3 enabled
    assert m["japan_share"] == round(1 / 3, 4)
    assert m["top_region"] == "North America"
    gaps = coverage_gaps(m, {"minimum_japan_share": 0.12, "minimum_us_share": 0.18,
                             "max_single_region_share": 0.35})
    # tier1=66%は35%以上なので一次情報warningは出ない。北米66%>35%で地域集中warningが出る。
    assert any("地域集中" in g for g in gaps)
    assert not any("一次情報比率" in g for g in gaps)


# ---------- 企業開示分類（§17-36〜41） ----------

def test_classify_disclosure_types():
    assert classify_disclosure("通期業績予想の修正に関するお知らせ")["disclosure_type"] == "guidance_revision"
    assert classify_disclosure("自己株式の取得に係る事項の決定")["disclosure_type"] == "buyback"
    assert classify_disclosure("Acquisition of XYZ Corp", "8-K")["disclosure_type"] == "M&A"
    assert classify_disclosure("Quarterly Results", "10-Q")["disclosure_type"] == "earnings"
    low = classify_disclosure("代表者の異動に関するお知らせ（軽微）")
    assert low["disclosure_type"] == "executive_change" and low["materiality"] == "high"
    other = classify_disclosure("その他の定型的なお知らせ")
    assert other["disclosure_type"] == "other" and other["materiality"] == "low"


# ---------- Dedup 正規化（§12, §17-30〜37） ----------

def test_url_normalization_folds_scheme_www_slash_tracking_fragment():
    base = normalize_url("https://example.com/news/article-1")
    assert normalize_url("http://example.com/news/article-1") == base       # http→https
    assert normalize_url("https://www.example.com/news/article-1/") == base  # www＋末尾/
    assert normalize_url("https://example.com/news/article-1?utm_source=rss") == base  # tracking除去
    assert normalize_url("https://example.com/news/article-1#top") == base   # fragment除去


def test_url_normalization_keeps_distinct_articles_distinct():
    a = normalize_url("https://example.com/news/article-1")
    b = normalize_url("https://example.com/news/article-2")
    assert a != b                                   # 別記事は統合しない


# ---------- 並列取得（§17-1〜9） ----------

def _rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><description>d</description>"
        f"<pubDate>Tue, 21 Jul 2026 00:00:00 GMT</pubDate></item>" for t, u in items)
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>F</title>{body}</channel></rss>".encode()


def _make_env():
    root = tempfile.mkdtemp(prefix="par_")
    store = ArticleStore(root + "/store")
    index = ArticleIndex(root + "/store/indexes/idx.sqlite")
    cursors = CursorStore(root + "/store/cursors/c.json")
    return store, index, cursors


def _sources(n):
    return [{"id": f"s{i:02}", "name": f"Src{i}", "url": f"https://h{i}.example/feed",
             "trust": 0.6, "country": "US"} for i in range(n)]


def _transport_factory(fail_ids=()):
    def transport(url, headers, timeout):
        host = url.split("//")[1].split(".")[0]          # h{i}
        idx = host[1:]
        if idx in fail_ids:
            raise ConnectionError("boom")
        items = [(f"Src{idx} article {j}", f"https://h{idx}.example/a{j}") for j in range(2)]
        return 200, {"etag": f'"{idx}"'}, _rss(items)
    return transport


def test_parallel_matches_sequential_results():
    srcs = _sources(6)
    # 逐次
    s1, i1, c1 = _make_env()
    seq = run_live_ingestion_all(srcs, s1, i1, c1, {}, NOW, transport=_transport_factory(),
                                 max_workers=1)
    # 並列
    s2, i2, c2 = _make_env()
    par = run_live_ingestion_all(srcs, s2, i2, c2, {}, NOW, transport=_transport_factory(),
                                 max_workers=6, per_host_max=2)
    # 決定的順序（source id順）で完全一致
    assert [r["source"] for r in seq] == [r["source"] for r in par]
    assert sum(r["new"] for r in seq) == sum(r["new"] for r in par) == 12
    assert i1.count() == i2.count() == 12            # SQLite書き込み整合（単一writer）


def test_parallel_isolates_one_source_failure():
    srcs = _sources(6)
    s, i, c = _make_env()
    res = run_live_ingestion_all(srcs, s, i, c, {}, NOW,
                                 transport=_transport_factory(fail_ids=("3",)),
                                 max_workers=6, per_host_max=2)
    statuses = {r["source"]: r["status"] for r in res}
    assert statuses["Src3"] == "failed"              # 1ソース失敗
    assert sum(1 for r in res if r["status"] == "success") == 5  # 他は継続
    assert i.count() == 10                            # 失敗分以外は保存


def test_parallel_deterministic_order_across_runs():
    srcs = list(reversed(_sources(6)))               # 入力順を変えても
    s, i, c = _make_env()
    res = run_live_ingestion_all(srcs, s, i, c, {}, NOW, transport=_transport_factory(),
                                 max_workers=6)
    assert [r["source"] for r in res] == [f"Src{n}" for n in range(6)]  # id順に整列
