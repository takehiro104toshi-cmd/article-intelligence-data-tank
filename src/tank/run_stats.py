"""Run statistics（§13）。取得実行ごとの統計を組み立て、atomic に保存する。

Secret / Token / 完全な内部例外スタックトレースは出さない（§13）。各ソースの
last_error は短い要約文字列のみ（fetcher が切り詰め済み）。
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


def new_run_id(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]


# ---------- Run status（Production Stabilization §2, §4） ----------
#
# healthy  … Package生成成功・重大なデータ破損なし（全ソース成功/未更新でも一部失敗でも可）
# degraded … 有効なPackageは存在するが、一部ソース失敗・新着0・market_reaction 0 等
#            の警告要因がある。Data Tankとしては利用可能。
# failed   … 新旧どちらのPackageも利用不能（全ソース失敗で非公開/validation失敗 等）。
#
# 終了コードは resolve_exit_code():
#   healthy/degraded → 0（一部ソース失敗で赤くしない）
#   failed          → 1（致命的障害のみ）
#   ※ exit 2 は CLI 引数不正のみ（argparse が自動で返す）。degraded を 2 で表さない。

def compute_run_status(
    *,
    enabled_count: int,
    failed_count: int,
    unchanged_count: int,
    success_count: int,
    all_failed: bool,
    publication_ok: bool,
    package_published: bool,
) -> str:
    """run_status（healthy/degraded/failed）を判定する（純関数・テスト可能）。"""
    if not package_published:
        # Package未公開: 全ソース失敗で保護スキップ、または publication 失敗。
        return "failed"
    if not publication_ok:
        return "failed"
    # ここから publication 成功。degraded 要因を評価する。
    degraded = (
        failed_count > 0            # 一部ソース失敗（403/429/timeout 等）
        or all_failed               # （publication成功でここには通常来ないが保険）
        or success_count == 0       # 新着0・全件重複・全未更新
    )
    return "degraded" if degraded else "healthy"


def resolve_exit_code(run_status: str) -> int:
    """run_status → 終了コード。healthy/degraded=0, failed=1（exit 2 は使わない）。"""
    return 1 if run_status == "failed" else 0


def build_run_stats(
    run_id: str,
    started_at: datetime,
    completed_at: datetime,
    configured_sources: int,
    enabled_sources: int,
    source_results: List[dict],
    total_tank_articles: int,
    package_items: int,
    package_size: int,
    clusters_created: int = 0,
    clusters_updated: int = 0,
    run_status: str = "healthy",
    index_rebuilt: bool = False,
    index_rebuilt_count: int = 0,
    index_rebuild_seconds: float = 0.0,
    retention_deleted_shards: int = 0,
    quarantine_count: int = 0,
    concentration: Optional[dict] = None,
    package_source_distribution: Optional[dict] = None,
) -> dict:
    successful = sum(1 for r in source_results if r.get("status") == "success")
    unchanged = sum(1 for r in source_results if r.get("status") == "unchanged")
    failed = sum(1 for r in source_results if r.get("status") == "failed")
    fetched = sum(int(r.get("fetched", 0)) for r in source_results)
    new_unique = sum(int(r.get("new", 0)) for r in source_results)
    exact_dups = sum(int(r.get("duplicates", 0)) for r in source_results)
    syndicated = sum(int(r.get("syndicated_duplicates", 0)) for r in source_results)
    date_inferred_total = sum(int(r.get("date_inferred", 0)) for r in source_results)
    stored = new_unique
    warnings = sum(1 for r in source_results if r.get("status") == "unchanged")
    errors = failed
    concentration = concentration or {}

    return {
        "run_id": run_id,
        "run_status": run_status,
        "started_at": started_at.astimezone(timezone.utc).isoformat(),
        "completed_at": completed_at.astimezone(timezone.utc).isoformat(),
        "total_seconds": round((completed_at - started_at).total_seconds(), 3),
        "configured_sources": configured_sources,
        "enabled_sources": enabled_sources,
        "successful_sources": successful,
        "unchanged_sources": unchanged,
        "failed_sources": failed,
        "fetched_items": fetched,
        "new_unique_articles": new_unique,
        "exact_duplicates": exact_dups,
        "syndicated_duplicates": syndicated,
        "date_anomalies": date_inferred_total,
        "clusters_created": clusters_created,
        "clusters_updated": clusters_updated,
        "stored_articles": stored,
        "total_tank_articles": total_tank_articles,
        "index_rebuilt": index_rebuilt,
        "index_rebuilt_count": index_rebuilt_count,
        "index_rebuild_seconds": round(index_rebuild_seconds, 3),
        "retention_deleted_shards": retention_deleted_shards,
        "quarantine_count": quarantine_count,
        "top_source": concentration.get("top_source", ""),
        "top_source_new_count": concentration.get("top_source_new_count", 0),
        "top_source_share": concentration.get("top_source_share", 0.0),
        "source_concentration": concentration.get("concentration_status", "ok"),
        "package_source_distribution": package_source_distribution or {},
        "package_items": package_items,
        "package_size": package_size,
        "warning_count": warnings,
        "error_count": errors,
        "sources": [
            {
                "source": r.get("source", ""),
                "status": r.get("status", "unknown"),
                "http_status": r.get("http_status", 0),
                "fetched": r.get("fetched", 0),
                "new": r.get("new", 0),
                "duplicates": r.get("duplicates", 0),
                "error": r.get("error", ""),
            }
            for r in source_results
        ],
    }


def save_run_stats(stats_dir: str, stats: dict) -> Path:
    """run統計を statistics/run_<id>.json と statistics/latest_run.json へ atomic 保存。"""
    d = Path(stats_dir)
    d.mkdir(parents=True, exist_ok=True)
    for name in (f"run_{stats['run_id']}.json", "latest_run.json"):
        path = d / name
        fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".stats-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return d / "latest_run.json"
