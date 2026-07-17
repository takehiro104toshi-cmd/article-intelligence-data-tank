#!/usr/bin/env python3
"""Article Intelligence Data Tank — 増分取得〜配信パッケージ生成のCLIエントリーポイント。

使い方:
    python scripts/run_ingestion.py                  # config.yaml の sources を取得・配信
    python scripts/run_ingestion.py --dry-run        # 取得のみ、配信物は書かない

sources が空（既定）の場合は新着0件として高速終了し、既存の published/latest/ は
変更しない（安全側デフォルト）。実運用では config.yaml の sources へ公開RSS/公式APIの
URLを追加し、_fetch_source() 内の実フェッチャー（RSS取得等）を実装してください。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tank.cluster import EventCluster  # noqa: E402
from tank.cursor import CursorStore  # noqa: E402
from tank.historical import find_historical_matches  # noqa: E402
from tank.index import ArticleIndex  # noqa: E402
from tank.ingestion import run_ingestion_all  # noqa: E402
from tank.market_reaction import MarketReactionStore  # noqa: E402
from tank.models import SourceCursor  # noqa: E402
from tank.publication import build_package, publish_package  # noqa: E402
from tank.quality import build_quality_metrics, build_tank_status  # noqa: E402
from tank.storage import ArticleStore  # noqa: E402


def _fetch_source(source_cfg: dict, cursor: SourceCursor) -> List[dict]:
    """公開RSS/公式APIから新着記事を取得する差し替え口。

    本実装はネットワークアクセスを行わない安全な既定（空リスト）。実運用では
    requests + feedparser 等で source_cfg["url"] から取得する処理をここへ実装する
    （公開RSS・公開API・公式発表のみ。有料/ログイン必須は対象外）。
    """
    return []


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Article Intelligence Data Tank ingestion runner")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    tank_cfg = config.get("article_tank", {})
    pub_cfg = config.get("publication", {})
    sources = config.get("sources", [])

    now = datetime.now(timezone.utc)
    data_dir = ROOT / config.get("data_dir", "data/article_store")
    published_dir = ROOT / config.get("published_dir", "published/latest")

    store = ArticleStore(str(data_dir))
    index = ArticleIndex(str(data_dir / "indexes" / "article_index.sqlite"))
    cursor_store = CursorStore(str(data_dir / "cursors" / "source_cursors.json"))
    reaction_store = MarketReactionStore(str(data_dir / "clusters" / "market_reactions.json"))

    if not sources:
        print("sources が未設定のため、新着記事の取得をスキップします（高速終了）。")
        summaries = []
    else:
        clusters: dict = {}
        summaries = run_ingestion_all(
            sources, _fetch_source, store, index, cursor_store, clusters, now,
            overlap_hours=tank_cfg.get("overlap_hours", 48),
        )
        store.write_manifest()

    print(json.dumps({"ingestion_summary": summaries}, ensure_ascii=False, indent=2))

    if args.dry_run:
        print("--dry-run のため配信パッケージ生成をスキップします。")
        index.close()
        return 0

    # 配信パッケージ生成（記事0件でも安全に空パッケージを生成する）
    tank_status = build_tank_status(index, now)
    empty_quality = {
        "freshness_score": 0.0, "diversity_score": 0.0, "source_quality_score": 0.0,
        "market_reaction_coverage": 0.0, "ai_semiconductor_share": 0.0,
    }
    package = build_package(
        articles=[], clusters={}, cursors=cursor_store.load_all(),
        reaction_store=reaction_store, historical_matches=[],
        tank_status=tank_status, quality=empty_quality, now=now,
        limits={
            "max_hot_articles": pub_cfg.get("max_hot_articles", 100),
            "max_global_drivers": pub_cfg.get("max_global_drivers", 20),
            "max_market_reactions": pub_cfg.get("max_market_reactions", 30),
            "max_risk_items": pub_cfg.get("max_risk_items", 20),
            "max_theme_summary": pub_cfg.get("max_theme_summary", 30),
            "max_event_clusters": pub_cfg.get("max_event_clusters", 30),
            "max_historical_matches": pub_cfg.get("max_historical_matches", 20),
            "max_source_health": pub_cfg.get("max_source_health", 100),
            "package_max_uncompressed_mb": pub_cfg.get("package_max_uncompressed_mb", 5),
        },
    )
    manifest = publish_package(package, str(published_dir))
    print(json.dumps({"publication_manifest": manifest}, ensure_ascii=False, indent=2))
    index.close()
    return 0 if manifest.get("publication_status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
