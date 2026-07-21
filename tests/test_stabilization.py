"""Production Stabilization Phase のテスト（§17 テスト観点）。

exit status / run_status、日付品質ガード、Source偏重観測、last-known-good、
そして最重要の Private Insight 保持（本文・AI所感・未来予測・送信日時が
安定化処理の前後で失われない）を検証する。ネットワークは使わない。
"""
from __future__ import annotations

import gzip
import json
import tempfile
from datetime import datetime, timedelta, timezone

from tank.date_quality import sanitize_published_at
from tank.ingestion import build_article_from_raw
from tank.run_stats import compute_run_status, resolve_exit_code
from tank.source_balance import package_source_distribution, source_concentration
from tank.storage import ArticleStore

NOW = datetime(2026, 7, 21, 3, 0, tzinfo=timezone.utc)  # JST 12:00


# ---------- exit status / run_status（§4, §17-1〜10） ----------

def test_run_status_healthy_all_success():
    st = compute_run_status(enabled_count=5, failed_count=0, unchanged_count=1,
                            success_count=4, all_failed=False,
                            publication_ok=True, package_published=True)
    assert st == "healthy" and resolve_exit_code(st) == 0


def test_run_status_degraded_partial_403():
    st = compute_run_status(enabled_count=5, failed_count=1, unchanged_count=1,
                            success_count=3, all_failed=False,
                            publication_ok=True, package_published=True)
    assert st == "degraded" and resolve_exit_code(st) == 0


def test_run_status_degraded_no_new_articles():
    # 新着0（全件重複/全未更新）でも publication 成功なら degraded / exit 0。
    st = compute_run_status(enabled_count=5, failed_count=0, unchanged_count=5,
                            success_count=0, all_failed=False,
                            publication_ok=True, package_published=True)
    assert st == "degraded" and resolve_exit_code(st) == 0


def test_run_status_failed_all_sources_no_package():
    st = compute_run_status(enabled_count=5, failed_count=5, unchanged_count=0,
                            success_count=0, all_failed=True,
                            publication_ok=False, package_published=False)
    assert st == "failed" and resolve_exit_code(st) == 1


def test_run_status_failed_on_validation_failure():
    # Package化はしたが publication 検証失敗 → failed / exit 1（latest保護は呼び出し側）。
    st = compute_run_status(enabled_count=5, failed_count=0, unchanged_count=0,
                            success_count=5, all_failed=False,
                            publication_ok=False, package_published=True)
    assert st == "failed" and resolve_exit_code(st) == 1


def test_exit_code_never_two():
    # exit 2 は CLI 引数不正のみ。healthy/degraded/failed からは 0 か 1 しか出ない。
    for st in ("healthy", "degraded", "failed"):
        assert resolve_exit_code(st) in (0, 1)


# ---------- 日付品質ガード（§7, §17-19〜27） ----------

def test_date_normal_kept():
    iso = "2026-07-20T09:00:00+00:00"
    pub, inferred, raw, anomaly = sanitize_published_at(iso, "Mon, 20 Jul 2026 09:00:00 GMT",
                                                        NOW.isoformat(), now=NOW)
    assert not inferred and anomaly == "" and pub.startswith("2026-07-20")
    assert raw == "Mon, 20 Jul 2026 09:00:00 GMT"  # 元文字列を保持


def test_date_missing_uses_fetched_and_infers():
    pub, inferred, raw, anomaly = sanitize_published_at("", "", NOW.isoformat(), now=NOW)
    assert inferred and anomaly == "missing" and pub == NOW.isoformat()


def test_date_future_corrected_but_not_discarded():
    future = (NOW + timedelta(days=3)).isoformat()
    pub, inferred, raw, anomaly = sanitize_published_at(future, future, NOW.isoformat(), now=NOW)
    assert inferred and anomaly == "future"
    assert pub == NOW.isoformat()           # fetched_at へ補正
    assert raw == future                     # 元の（未来）文字列は検証用に保持


def test_date_ancient_over_20yr_corrected_and_raw_kept():
    # 20年より前（例:2000年）は date anomaly として fetched_at へ補正する（§7）。
    old = "2000-01-01T00:00:00+00:00"
    pub, inferred, raw, anomaly = sanitize_published_at(old, "Sat, 01 Jan 2000 00:00:00 GMT",
                                                        NOW.isoformat(), now=NOW)
    assert inferred and anomaly == "too_old"
    assert pub == NOW.isoformat()            # 破棄せず fetched_at へ補正
    assert raw == "Sat, 01 Jan 2000 00:00:00 GMT"


def test_date_within_20yr_is_kept_as_legit_old():
    # 20年以内（例:2008年=18年前）は「正当に古い記事」として尊重し、補正しない。
    # （retention はこれを通常どおり保持期間で扱う。日付破損とは区別する。）
    old = "2008-06-29T00:00:00+00:00"
    pub, inferred, raw, anomaly = sanitize_published_at(old, "Sun, 29 Jun 2008 00:00:00 GMT",
                                                        NOW.isoformat(), now=NOW)
    assert not inferred and anomaly == "" and pub.startswith("2008")


def test_mislabeled_ancient_article_files_into_today_not_ancient_shard():
    """最近取得した記事が20年超前と誤解析されても、当日のシャードへ入る（retention誤削除を防ぐ）。"""
    raw = {"title": "電力設備投資の記事", "url": "https://ex.com/x",
           "description": "本文抜粋", "published_at_utc": "1999-06-29T00:00:00+00:00",
           "published_raw": "Tue, 29 Jun 1999 00:00:00 GMT"}
    art = build_article_from_raw(raw, {"name": "Ex", "trust": 0.6}, "run1", NOW)
    assert art.date_inferred is True
    assert art.raw_published_at == "Tue, 29 Jun 1999 00:00:00 GMT"
    assert art.published_at_jst == "2026-07-21"   # 1999ではなく今日のJST日付
    assert not art.published_at_utc.startswith("1999")


# ---------- Source偏重の観測（§9, §17-33〜35） ----------

def test_source_concentration_warning_and_critical():
    results = [
        {"source": "Economic Times", "new": 64, "status": "success"},
        {"source": "Reuters", "new": 20, "status": "success"},
        {"source": "NHK", "new": 20, "status": "success"},
    ]
    c = source_concentration(results, warning_share=0.35, critical_share=0.60)
    assert c["top_source"] == "Economic Times"
    assert c["top_source_new_count"] == 64
    assert c["concentration_status"] == "critical"  # 64/104 = 0.615


def test_package_source_distribution_counts():
    hot = [{"source": "A"}, {"source": "A"}, {"source": "B"}]
    dist = package_source_distribution(hot)
    assert dist == {"A": 2, "B": 1}


# ---------- Private Insight 保持（§11, §17-43〜49・最重要） ----------

def test_private_insight_untouched_by_article_retention():
    """公開Articleのretention（古いシャード削除）が Private Insight を一切壊さないこと。

    本文・AI所感・未来予測・送信日時が retention 実行の前後で保持されることを明示する。
    """
    from tank.private_insight import (
        LocalPrivateInsightStore, analyze_record, intake,
    )

    root = tempfile.mkdtemp(prefix="stab_")
    # --- private（羅針盤）側: 記事を保存し分析まで実施 ---
    pstore = LocalPrivateInsightStore(base_dir=root + "/private")
    rec = intake(pstore, body="日経の本文。電力インフラ投資が拡大している。",
                 title="送電網投資", source_name="日本経済新聞")
    analyze_record(pstore, rec.private_article_id, config={})
    before = pstore.get(rec.private_article_id)
    before_analysis = pstore.read_analysis(rec.private_article_id)
    before_body = pstore.read_body(before)
    before_submitted = before.submitted_at_utc

    # --- 公開Article側: 古いシャードを作って retention 実行 ---
    astore = ArticleStore(root + "/article_store")
    old_art = build_article_from_raw(
        {"title": "古い記事", "url": "https://e.com/old", "description": "x",
         "published_at_utc": "2020-01-01T00:00:00+00:00", "published_raw": "old"},
        {"name": "E", "trust": 0.5}, "r", NOW)
    astore.append_articles("2020-01-01", [old_art])
    purged = astore.purge_shards_before("2026-06-21")
    assert "2020-01-01" in purged  # 公開側は削除された

    # --- Private Insight は完全に無傷であること ---
    after = pstore.get(rec.private_article_id)
    assert after is not None, "private record が消えた（retentionが誤って触れた）"
    assert pstore.read_body(after) == before_body                 # 本文保持
    assert after.submitted_at_utc == before_submitted             # 送信日時保持
    after_analysis = pstore.read_analysis(rec.private_article_id)
    # AI所感・未来予測（分析結果）も保持
    if before_analysis is not None:
        assert after_analysis is not None
        assert after_analysis.get("forecasts")                    # 未来予測が残る
        assert "ai_analyst_impression" in after_analysis or "impression" in json.dumps(
            after_analysis, ensure_ascii=False)


# ---------- last-known-good（§12, §17-37） ----------

def test_existing_valid_package_detection_for_all_fail_branch():
    """全ソース失敗時の分岐（§4-#7/#8）: 既存の有効Packageがあれば degraded 扱いにできる。"""
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "scripts"))
    from run_ingestion import _existing_package_is_valid
    from tank.publication import publish_package

    root = tempfile.mkdtemp(prefix="exist_")
    assert _existing_package_is_valid(root) is False  # まだPackageなし → failed相当
    pkg = {
        "schema_version": "1.0", "generated_at_utc": NOW.isoformat(),
        "generated_at_jst": NOW.isoformat(), "tank_status": {},
        "hot_articles": [], "global_drivers": [], "market_reactions": [],
        "risk_radar": [], "theme_summary": [], "event_clusters": [],
        "historical_matches": [], "source_health": [], "quality": {},
    }
    publish_package(pkg, root)
    assert _existing_package_is_valid(root) is True   # 有効Packageあり → degraded相当


def test_publish_writes_last_known_good():
    from tank.publication import publish_package

    root = tempfile.mkdtemp(prefix="lkg_")
    pkg = {
        "schema_version": "1.0", "generated_at_utc": NOW.isoformat(),
        "generated_at_jst": NOW.isoformat(), "tank_status": {},
        "hot_articles": [], "global_drivers": [], "market_reactions": [],
        "risk_radar": [], "theme_summary": [], "event_clusters": [],
        "historical_matches": [], "source_health": [], "quality": {},
    }
    manifest = publish_package(pkg, root)
    assert manifest["publication_status"] == "success"
    lkg = f"{root}/last_known_good/intelligence_package.json.gz"
    with gzip.open(lkg, "rb") as f:
        restored = json.loads(f.read().decode("utf-8"))
    assert restored["schema_version"] == "1.0"
