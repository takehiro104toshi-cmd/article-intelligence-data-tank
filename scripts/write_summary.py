#!/usr/bin/env python3
"""GitHub Actions Job Summary 生成（Production Stabilization §15 Observability）。

data/article_store/statistics/latest_run.json を読み、run_status・取得・保存・
品質・配信・Private Insight storage health を Markdown で標準出力へ書く
（workflow が $GITHUB_STEP_SUMMARY へリダイレクトする）。

Private Insight は「件数」と「storage health」だけを出し、本文・タイトル・
送信内容は一切表示しない（§11, §15）。統計JSONが無い場合も落とさない。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATS = ROOT / "data" / "article_store" / "statistics" / "latest_run.json"
PRIVATE_DIR = ROOT / "data" / "private_insights"

_STATUS_BADGE = {"healthy": "🟢 HEALTHY", "degraded": "🟡 DEGRADED", "failed": "🔴 FAILED"}


def _private_storage_health() -> dict:
    """private領域の「健全性」だけを返す（本文・タイトルは読まない・出さない）。

    このワークフロー(article-tank-update)は private_insights を一切処理しないため、
    ここでは「ディレクトリが存在し、公開領域と分離されているか」の確認に留める。
    件数は index/*.json のファイル数のみ（本文は開かない）。
    """
    if not PRIVATE_DIR.exists():
        return {"present": False, "count": 0, "health": "not_present"}
    # index配下のメタJSON数だけ数える（本文.encや分析は開かない）。
    idx = PRIVATE_DIR / "index"
    count = len(list(idx.glob("*.json"))) if idx.exists() else 0
    return {"present": True, "count": count, "health": "ok"}


def main() -> int:
    lines = ["### 🗄️ Article Tank Update 実行結果", ""]

    if not STATS.exists():
        lines.append("- ⚠️ run統計(latest_run.json)が見つかりません。")
        if (ROOT / "published" / "latest" / "manifest.json").exists():
            lines.append("- ✅ published/latest/manifest.json は存在します。")
        print("\n".join(lines))
        return 0

    try:
        s = json.loads(STATS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        lines.append("- ⚠️ run統計の読み込みに失敗しました。")
        print("\n".join(lines))
        return 0

    status = s.get("run_status", "unknown")
    lines += [
        f"**Run status: {_STATUS_BADGE.get(status, status.upper())}**",
        "",
        "#### Ingestion",
        f"- configured: {s.get('configured_sources',0)} / enabled: {s.get('enabled_sources',0)} / "
        f"success: {s.get('successful_sources',0)} / unchanged: {s.get('unchanged_sources',0)} / "
        f"failed: {s.get('failed_sources',0)}",
        f"- fetched: {s.get('fetched_items',0)} / new_unique: {s.get('new_unique_articles',0)} / "
        f"duplicates: {s.get('exact_duplicates',0)} / stored: {s.get('stored_articles',0)}",
        "",
        "#### Storage",
        f"- total_articles: {s.get('total_tank_articles',0)} / "
        f"index_rebuilt: {s.get('index_rebuilt',False)} "
        f"({s.get('index_rebuilt_count',0)}件 / {s.get('index_rebuild_seconds',0)}秒)",
        f"- retention_deleted_shards: {s.get('retention_deleted_shards',0)} / "
        f"quarantine: {s.get('quarantine_count',0)} / date_anomalies(補正): {s.get('date_anomalies',0)}",
        "",
        "#### Quality",
        f"- top_source: {s.get('top_source','') or '—'} "
        f"({s.get('top_source_new_count',0)}件 / share {s.get('top_source_share',0)*100:.0f}%) "
        f"→ concentration: {s.get('source_concentration','ok')}",
        "",
        "#### Publication",
        f"- package_items: {s.get('package_items',0)} / compressed_size: {s.get('package_size',0)} bytes",
    ]

    ph = _private_storage_health()
    lines += [
        "",
        "#### Private Insight（本文・タイトルは非表示）",
        f"- storage: {'分離領域あり' if ph['present'] else '未使用(このrunでは非処理)'} / "
        f"health: {ph['health']} / index_records: {ph['count']}",
        "- ※このワークフローは private 領域を読み書きしません（公開処理から完全分離）。",
    ]

    if status == "degraded":
        lines += ["", "> ℹ️ DEGRADED: 一部ソース失敗/新着0等の警告要因はありますが、"
                  "有効なPackageを公開済みでData Tankとして利用可能です（exit 0）。"]
    elif status == "failed":
        lines += ["", "> 🔴 FAILED: 有効なPackageを生成できませんでした。既存の latest / "
                  "last_known_good は保護されています（exit 1）。"]

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
