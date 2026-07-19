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
) -> dict:
    successful = sum(1 for r in source_results if r.get("status") == "success")
    unchanged = sum(1 for r in source_results if r.get("status") == "unchanged")
    failed = sum(1 for r in source_results if r.get("status") == "failed")
    fetched = sum(int(r.get("fetched", 0)) for r in source_results)
    new_unique = sum(int(r.get("new", 0)) for r in source_results)
    exact_dups = sum(int(r.get("duplicates", 0)) for r in source_results)
    syndicated = sum(int(r.get("syndicated_duplicates", 0)) for r in source_results)
    stored = new_unique
    warnings = sum(1 for r in source_results if r.get("status") == "unchanged")
    errors = failed

    return {
        "run_id": run_id,
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
        "clusters_created": clusters_created,
        "clusters_updated": clusters_updated,
        "stored_articles": stored,
        "total_tank_articles": total_tank_articles,
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
