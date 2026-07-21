"""Rashinban Private Insight Vault（Data Tank側）のオフラインテスト。

Intake/Storage/dedup/削除/分析/未来予測/confidence上限/派生情報のallowlist/
Worker同期（fake transport）/gitignore を検証する。ネットワークは使わない。
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tank.private_insight import (
    CONFIDENCE_CAPS,
    DERIVED_FORBIDDEN_KEYS,
    LocalPrivateInsightStore,
    LocalRuleBasedFallbackAdapter,
    analyze_record,
    build_derived_summary,
    intake,
    sync_and_analyze_from_worker,
)

BODY = (
    "データセンター向けの送電網投資が拡大している。電力会社は変圧器や電線の調達を増やし、"
    "発電と送配電の設備投資計画を引き上げた。AI需要の拡大が電力インフラ全体へ波及している。"
)


def _store(tmp_path) -> LocalPrivateInsightStore:
    return LocalPrivateInsightStore(str(tmp_path / "private_insights"))


# ---------- Intake / Storage ----------

def test_intake_body_only_saves_with_server_timestamps(tmp_path):
    store = _store(tmp_path)
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    rec = intake(store, BODY, now=now)
    assert rec.status == "stored"
    assert rec.private_article_id.startswith("pai_")
    assert rec.request_id.startswith("req_")
    assert rec.submitted_at_utc.startswith("2026-07-20T12:00")
    assert rec.submitted_at_jst.startswith("2026-07-20T21:00")  # JST=UTC+9
    assert rec.character_count == len(BODY)
    assert rec.visibility == "private"
    # raw本文とindexが分離されている（indexに本文が無い）
    index_text = (store.base / "index.json").read_text(encoding="utf-8")
    assert BODY[:20] not in index_text
    assert store.read_body(rec) == BODY


def test_intake_rejects_empty_and_oversized_body(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="empty_body"):
        intake(store, "   ")
    with pytest.raises(ValueError, match="body_too_long"):
        intake(store, "あ" * 100, max_body_chars=50)


def test_duplicate_submission_appends_history_not_new_body(tmp_path):
    store = _store(tmp_path)
    first = intake(store, BODY, user_note="1回目")
    second = intake(store, BODY, user_note="2回目メモ")
    assert second.status == "duplicate"
    assert second.private_article_id == first.private_article_id
    stored = store.get(first.private_article_id)
    assert len(stored.submitted_history) == 2
    assert "2回目メモ" in stored.user_note  # メモは追記される
    # raw本文ファイルは1つだけ
    raws = list((store.base / "raw").rglob("*.txt"))
    assert len(raws) == 1


def test_soft_delete_keeps_body_permanent_delete_removes_it(tmp_path):
    store = _store(tmp_path)
    rec = intake(store, BODY)
    assert store.delete(rec.private_article_id, permanent=False, reason="test")
    soft = store.get(rec.private_article_id)
    assert soft.delete_type == "soft" and soft.deleted_at
    assert store.read_body(soft) == BODY  # soft deleteでは本文は残る
    assert soft.private_article_id not in [r.private_article_id for r in store.list_records()]

    assert store.delete(rec.private_article_id, permanent=True)
    hard = store.get(rec.private_article_id)
    assert hard.delete_type == "permanent"
    assert hard.body_available is False
    assert list((store.base / "raw").rglob("*.txt")) == []  # 本文ファイル削除


def test_private_dir_is_gitignored():
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "check-ignore", "data/private_insights/raw/2026/07/x.txt"],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    assert result.returncode == 0  # 無視されている＝コミットされない


# ---------- 分析（ルールベース） ----------

def test_rule_based_analysis_separates_facts_impression_forecasts(tmp_path):
    store = _store(tmp_path)
    rec = intake(store, BODY, title="データセンター向け送電網投資が拡大")
    analysis = analyze_record(store, rec.private_article_id, config={"provider": "rule_based"})
    assert analysis is not None
    assert analysis["model_provider"] == "rule_based"
    # 事実・所感・予測がキーとして分離されている
    assert "facts" in analysis["basic"]
    assert "AI Analyst Impression" in analysis["ai_analyst_impression"]["label"]
    assert len(analysis["forecasts"]) == 4
    # 分類は電力系
    assert analysis["classification"]["primary_category"] in ("electric_power", "ai", "semiconductor")
    # storeに保存されstatusがcompletedになる
    assert store.get(rec.private_article_id).status == "completed"
    assert store.read_analysis(rec.private_article_id) is not None


def test_forecasts_have_scenarios_triggers_and_review_date(tmp_path):
    store = _store(tmp_path)
    rec = intake(store, BODY, title="送電網投資")
    analysis = analyze_record(store, rec.private_article_id, config={"provider": "rule_based"})
    types = {f["scenario_type"] for f in analysis["forecasts"]}
    assert types == {"base", "upside", "downside", "tail_risk"}
    for f in analysis["forecasts"]:
        assert f["horizon"] in ("1w", "1m", "3m", "1y", "3-5y")
        assert f["invalidation_triggers"], "invalidation triggerは必須"
        assert f["next_review_date"]
        assert f["validation_status"] == "pending"
        assert f["evidence_level"] == "article_based_hypothesis"


def test_confidence_never_exceeds_article_only_cap(tmp_path):
    store = _store(tmp_path)
    rec = intake(store, BODY, title="送電網投資")
    analysis = analyze_record(store, rec.private_article_id, config={"provider": "rule_based"})
    cap = CONFIDENCE_CAPS["article_based_hypothesis"]
    for f in analysis["forecasts"]:
        assert 0.0 <= f["confidence"] <= cap


def test_analysis_survives_missing_llm_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = _store(tmp_path)
    rec = intake(store, BODY)
    # provider未指定＝anthropic優先だがキーが無い → rule_basedへフォールバックし、記事は失われない
    analysis = analyze_record(store, rec.private_article_id, config={})
    assert analysis is not None
    assert analysis["model_provider"] == "rule_based"
    assert store.read_body(store.get(rec.private_article_id)) == BODY


# ---------- 派生情報（allowlist） ----------

def test_derived_summary_excludes_raw_body_and_secrets(tmp_path):
    store = _store(tmp_path)
    rec = intake(store, BODY, title="送電網投資", user_note="社外秘メモ")
    analysis = analyze_record(store, rec.private_article_id, config={"provider": "rule_based"})
    derived = build_derived_summary(rec, analysis)

    blob = json.dumps(derived, ensure_ascii=False)
    assert BODY[:15] not in blob            # 本文が出ない
    assert "社外秘メモ" not in blob          # user_noteも出ない
    assert rec.raw_body_storage_key not in blob  # storage keyも出ない
    for key in DERIVED_FORBIDDEN_KEYS:
        assert key not in derived
    # 必要な派生情報は含まれる
    assert derived["title"] and derived["forecast_summary"]
    assert derived["next_review_date"]


# ---------- Worker同期（fake transport） ----------

def test_worker_sync_analyzes_queue_and_posts_derived(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls = []

    def fake_transport(method, url, payload=None):
        calls.append((method, url, payload))
        if method == "GET":
            return {"items": [{
                "private_article_id": "pai_x1", "title": "送電網投資", "body": BODY,
                "submitted_at_utc": "2026-07-20T12:00:00+00:00",
            }]}
        return {"ok": True}

    result = sync_and_analyze_from_worker("https://w.example/api/private-insight", "tok",
                                          transport=fake_transport)
    assert result == {"fetched": 1, "analyzed": 1, "failed": 0}
    post = next(c for c in calls if c[0] == "POST")
    assert post[1].endswith("/analysis/pai_x1")
    payload = post[2]
    assert payload["status"] == "completed"
    assert BODY[:15] not in json.dumps(payload["derived"], ensure_ascii=False)  # derivedへ本文が漏れない
    assert payload["analysis"]["forecasts"]


def test_worker_sync_marks_failed_without_stopping(tmp_path):
    def fake_transport(method, url, payload=None):
        if method == "GET":
            return {"items": [{"private_article_id": "pai_bad", "body": ""}]}  # 本文欠落
        return {"ok": True}

    result = sync_and_analyze_from_worker("https://w.example/api", "tok", transport=fake_transport)
    assert result["failed"] == 1
    assert result["analyzed"] == 0
