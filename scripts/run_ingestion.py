#!/usr/bin/env python3
"""Article Intelligence Data Tank — 増分取得〜配信パッケージ生成のCLIエントリーポイント。

使い方:
    python scripts/run_ingestion.py                 # 取得→分類→クラスタ→保存→配信Package生成
    python scripts/run_ingestion.py --dry-run       # 取得・保存まで（配信Packageは生成しない）
    python scripts/run_ingestion.py --verify        # 各enabledソースの到達性だけ確認（保存しない）

ニュースソースは config/sources.yaml（推奨）または config.yaml の sources: から読み込む。
enabled: true のソースだけを実際にHTTP取得する。1ソースの障害では全体を止めず、
全ソース障害時は既存の published/latest/ を空Packageで上書きしない（§10）。

終了コード:
    0 = 成功（enabledソースが全て成功/未更新、Package公開済み）
    2 = degraded（一部ソース失敗だが公開データはありPackage公開）
    1 = 失敗（全ソース失敗で非公開、またはPackage生成失敗）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tank.cursor import CursorStore  # noqa: E402
from tank.fetcher import fetch_feed  # noqa: E402
from tank.index import ArticleIndex  # noqa: E402
from tank.ingestion import rebuild_index_from_store, run_live_ingestion_all  # noqa: E402
from tank.market_reaction import MarketReactionStore  # noqa: E402
from tank.publication import build_package, publish_package  # noqa: E402
from tank.quality import build_quality_metrics, build_tank_status  # noqa: E402
from tank.run_stats import build_run_stats, new_run_id, save_run_stats  # noqa: E402
from tank.source_config import enabled_sources, load_sources  # noqa: E402
from tank.storage import ArticleStore  # noqa: E402

_JST = timezone(timedelta(hours=9))


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _load_hot_articles(store: ArticleStore, now: datetime, hot_hours: int, cap: int = 5000) -> list:
    """配信Package用に、hot window（既定72h）内の記事だけを store から読み込む（全件は読まない）。"""
    start = (now - timedelta(hours=hot_hours)).astimezone(_JST).strftime("%Y-%m-%d")
    out = []
    for art in store.iter_shards(date_from=start):
        out.append(art)
        if len(out) >= cap:
            break
    return out


def _verify(sources: List[dict], timeout: int, retry: int) -> int:
    """各enabledソースへ1回だけアクセスし、到達性を報告する（保存しない・§6の事前確認用）。"""
    print(f"到達性チェック: {len(sources)} ソース\n")
    ok = fail = 0
    for src in sources:
        result = fetch_feed(src, cursor=None, timeout=timeout, retry=retry)
        if result.status == "ok":
            ok += 1
            print(f"  OK    {src['id']:24} items={len(result.articles):<4} http={result.http_status} {src['url']}")
        elif result.status == "not_modified":
            ok += 1
            print(f"  304   {src['id']:24} (not modified) {src['url']}")
        else:
            fail += 1
            print(f"  FAIL  {src['id']:24} http={result.http_status} {result.error}  {src['url']}")
    print(f"\n到達可能: {ok} / 失敗: {fail}")
    return 0 if fail == 0 else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Article Intelligence Data Tank ingestion runner")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--dry-run", action="store_true", help="取得・保存まで（配信Packageは生成しない）")
    parser.add_argument("--verify", action="store_true", help="各enabledソースの到達性のみ確認（保存しない）")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    tank_cfg = config.get("article_tank", {})
    pub_cfg = config.get("publication", {})

    timeout = int(tank_cfg.get("source_timeout_seconds", 12))
    retry = int(tank_cfg.get("source_retry_count", 1))
    overlap_hours = int(tank_cfg.get("overlap_hours", 48))
    hot_hours = int(tank_cfg.get("hot_hours", 72))
    max_items = int(tank_cfg.get("max_items_per_source", 200))

    # sources_file は config ファイルのあるディレクトリを基準に解決する。
    config_dir = Path(args.config).resolve().parent
    all_sources = load_sources(config, base_dir=config_dir)
    live_sources = enabled_sources(all_sources)

    if args.verify:
        if not live_sources:
            print("enabled なソースがありません（config/sources.yaml を確認してください）。")
            return 0
        return _verify(live_sources, timeout, retry)

    now = datetime.now(timezone.utc)
    run_id = new_run_id(now)
    data_dir = ROOT / config.get("data_dir", "data/article_store")
    published_dir = ROOT / config.get("published_dir", "published/latest")
    stats_dir = data_dir / "statistics"

    store = ArticleStore(str(data_dir))
    index = ArticleIndex(str(data_dir / "indexes" / "article_index.sqlite"))
    cursor_store = CursorStore(str(data_dir / "cursors" / "source_cursors.json"))
    reaction_store = MarketReactionStore(str(data_dir / "clusters" / "market_reactions.json"))

    # GitHub Actions のチェックアウトには SQLite 索引が含まれない（.gitignore）。
    # 索引が空でコミット済みシャードがある場合は、シャードから索引を再構築して
    # 実行間の増分取得・重複排除を成立させる。
    if index.count() == 0:
        restored = rebuild_index_from_store(store, index)
        if restored:
            print(f"SQLite索引が空のため、シャードから {restored} 件を再構築しました。")

    clusters: dict = {}
    if not live_sources:
        print("enabled なソースが未設定のため、取得をスキップします（config/sources.yaml を確認してください）。")
        source_results: List[dict] = []
    else:
        print(f"[{run_id}] {len(live_sources)} ソースを取得します...")

        def _log(res: dict) -> None:
            print(f"  {res['status']:9} {res['source']:24} "
                  f"http={res.get('http_status',0)} new={res.get('new',0)} "
                  f"dup={res.get('duplicates',0)} {res.get('error','')}")

        source_results = run_live_ingestion_all(
            live_sources, store, index, cursor_store, clusters, now,
            overlap_hours=overlap_hours, timeout=timeout, retry=retry,
            max_items=max_items, logger=_log,
        )
        store.write_manifest()

    # 保持期間（retention）: article_tank.retention_days（既定null=無期限）より古い
    # シャード・索引行を削除し、ストアの無制限な肥大化を防ぐ（§ retention）。
    # --verifyでは到達しない・--dry-runでも「取得・保存」の一部として実行する。
    retention_days = tank_cfg.get("retention_days")
    if retention_days is not None:
        cutoff_date = (now - timedelta(days=int(retention_days))).astimezone(_JST).strftime("%Y-%m-%d")
        purged_dates = store.purge_shards_before(cutoff_date)
        if purged_dates:
            purged_rows = index.delete_before(cutoff_date)
            store.write_manifest()
            print(
                f"保持期間（{retention_days}日）を超えたシャード{len(purged_dates)}件"
                f"（{purged_dates[0]}〜{purged_dates[-1]}）・索引{purged_rows}件を削除しました。"
            )

    # ソース健全性の集計
    enabled_count = len(live_sources)
    success = sum(1 for r in source_results if r.get("status") == "success")
    unchanged = sum(1 for r in source_results if r.get("status") == "unchanged")
    failed = sum(1 for r in source_results if r.get("status") == "failed")
    all_failed = enabled_count > 0 and success == 0 and unchanged == 0 and failed == enabled_count

    clusters_created = len(clusters)
    clusters_updated = sum(1 for c in clusters.values() if c.article_count > 1)
    quarantine_count = len(list(store.quarantine_dir.glob("*.corrupt")))

    completed = datetime.now(timezone.utc)

    # 統計を保存（Package生成前でも必ず残す）
    def _persist_stats(package_items: int, package_size: int) -> dict:
        stats = build_run_stats(
            run_id=run_id, started_at=now, completed_at=completed,
            configured_sources=len(all_sources), enabled_sources=enabled_count,
            source_results=source_results, total_tank_articles=index.count(),
            package_items=package_items, package_size=package_size,
            clusters_created=clusters_created, clusters_updated=clusters_updated,
        )
        save_run_stats(str(stats_dir), stats)
        return stats

    if args.dry_run:
        stats = _persist_stats(0, 0)
        print(json.dumps({"run_stats": stats}, ensure_ascii=False, indent=2))
        print("--dry-run のため配信パッケージ生成をスキップします。")
        index.close()
        return 0 if failed == 0 else 2

    # §10: 全ソース失敗時は既存Packageを空Packageで上書きしない（degraded/failureで終了）。
    if all_failed:
        stats = _persist_stats(0, 0)
        print(json.dumps({"run_stats": stats}, ensure_ascii=False, indent=2))
        print("全ソースの取得に失敗したため、配信Packageは更新しませんでした（既存Packageを保護）。")
        index.close()
        return 1

    # 実記事から配信Packageを生成（hot window の記事＋本 runのクラスタ）
    hot_articles = _load_hot_articles(store, now, hot_hours)
    tank_status = build_tank_status(index, now, quarantine_count=quarantine_count)
    quality = build_quality_metrics(hot_articles, index, reaction_coverage_count=0)

    package = build_package(
        articles=hot_articles, clusters=clusters, cursors=cursor_store.load_all(),
        reaction_store=reaction_store, historical_matches=[],
        tank_status=tank_status, quality=quality, now=now,
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

    package_items = sum(manifest.get("item_counts", {}).values()) if manifest.get("item_counts") else 0
    stats = _persist_stats(package_items, manifest.get("compressed_size", 0))

    print(json.dumps({"run_stats": stats, "publication_manifest": manifest}, ensure_ascii=False, indent=2))
    index.close()

    if manifest.get("publication_status") != "success":
        return 1
    return 2 if failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
