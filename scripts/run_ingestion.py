#!/usr/bin/env python3
"""Article Intelligence Data Tank — 増分取得〜配信パッケージ生成のCLIエントリーポイント。

使い方:
    python scripts/run_ingestion.py                 # 取得→分類→クラスタ→保存→配信Package生成
    python scripts/run_ingestion.py --dry-run       # 取得・保存まで（配信Packageは生成しない）
    python scripts/run_ingestion.py --verify        # 各enabledソースの到達性だけ確認（保存しない）

ニュースソースは config/sources.yaml（推奨）または config.yaml の sources: から読み込む。
enabled: true のソースだけを実際にHTTP取得する。1ソースの障害では全体を止めず、
全ソース障害時は既存の published/latest/ を空Packageで上書きしない（§10）。

終了コード（Production Stabilization §2, §4）:
    0 = healthy または degraded（有効なPackageがあり、Data Tankとして利用可能）
        ・一部ソースが403/429/timeout/500 でも、Packageが公開できていれば 0。
        ・新着0件・全件重複・market_reaction 0 でも、既存/新規Packageが有効なら 0。
    1 = failed（新旧どちらのPackageも利用不能：全ソース失敗で非公開、Package生成/検証失敗）。
    2 = CLI引数不正のみ（argparse が自動で返す。degraded を 2 で表さない）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tank.cursor import CursorStore  # noqa: E402
from tank.fetcher import build_user_agent, fetch_feed  # noqa: E402
from tank.index import ArticleIndex  # noqa: E402
from tank.ingestion import rebuild_index_from_store, run_live_ingestion_all  # noqa: E402
from tank.market_reaction import MarketReactionStore  # noqa: E402
from tank.publication import build_package, publish_package  # noqa: E402
from tank.quality import build_quality_metrics, build_tank_status  # noqa: E402
from tank.run_stats import (  # noqa: E402
    build_run_stats, compute_run_status, new_run_id, resolve_exit_code, save_run_stats,
)
from tank.source_balance import package_source_distribution, source_concentration  # noqa: E402
from tank.source_config import enabled_sources, load_sources  # noqa: E402
from tank.storage import ArticleStore  # noqa: E402

_JST = timezone(timedelta(hours=9))


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _existing_package_is_valid(published_dir: Path) -> bool:
    """既存の published/latest/ に、正常公開済みの有効なPackageがあるか判定する（§4, §12）。

    manifest.json が publication_status=success で、gz本体が存在し gzip展開・JSON parse
    できることを軽量に確認する。全ソース失敗時に「既存Packageを維持してdegraded/exit 0」と
    するか「failed/exit 1」とするかの分岐に使う。
    """
    import gzip as _gzip

    manifest_path = Path(published_dir) / "manifest.json"
    gz_path = Path(published_dir) / "intelligence_package.json.gz"
    if not manifest_path.exists() or not gz_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("publication_status") != "success":
            return False
        with _gzip.open(gz_path, "rb") as f:
            json.loads(f.read().decode("utf-8"))  # 展開＋parseできれば有効
        return True
    except (OSError, ValueError):
        return False


def _load_hot_articles(store: ArticleStore, now: datetime, hot_hours: int, cap: int = 5000) -> list:
    """配信Package用に、hot window（既定72h）内の記事だけを store から読み込む（全件は読まない）。"""
    start = (now - timedelta(hours=hot_hours)).astimezone(_JST).strftime("%Y-%m-%d")
    out = []
    for art in store.iter_shards(date_from=start):
        out.append(art)
        if len(out) >= cap:
            break
    return out


def _verify(sources: List[dict], timeout: int, retry: int, user_agent: str = "") -> int:
    """各enabledソースへ1回だけアクセスし、到達性を報告する（保存しない・§6の事前確認用）。"""
    print(f"到達性チェック: {len(sources)} ソース\n")
    ok = fail = 0
    for src in sources:
        result = fetch_feed(src, cursor=None, timeout=timeout, retry=retry,
                            user_agent=user_agent or None)
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
    if ok:
        print("→ OK/304 のソースは config/sources.yaml で enabled: true にして本番導入できます。")
    # --verify は到達性の「診断」。到達不能があってもコマンド自体は成功扱い（exit 0）。
    # exit 2 はCLI引数不正のみに限定する（§2）。
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Article Intelligence Data Tank ingestion runner")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--dry-run", action="store_true", help="取得・保存まで（配信Packageは生成しない）")
    parser.add_argument("--verify", action="store_true", help="各enabledソースの到達性のみ確認（保存しない）")
    parser.add_argument("--verify-candidates", action="store_true",
                        help="未有効(enabled:false)の候補ソースの到達性を確認（保存しない・§16-A）")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    tank_cfg = config.get("article_tank", {})
    pub_cfg = config.get("publication", {})

    timeout = int(tank_cfg.get("source_timeout_seconds", 12))
    retry = int(tank_cfg.get("source_retry_count", 1))
    overlap_hours = int(tank_cfg.get("overlap_hours", 48))
    hot_hours = int(tank_cfg.get("hot_hours", 72))
    max_items = int(tank_cfg.get("max_items_per_source", 200))
    # §2 並列取得: fetch: ブロック（無ければ article_tank.max_fetch_workers を後方互換で使用）。
    fetch_cfg = config.get("fetch", {})
    max_fetch_workers = int(fetch_cfg.get("max_fetch_workers", tank_cfg.get("max_fetch_workers", 6)))
    per_host_max = int(fetch_cfg.get("per_host_max_concurrency", 2))

    # §6 SEC等の「連絡先付きUser-Agent」要件: http: ブロックの user_agent_name と、
    # contact_email_env（既定 DATA_TANK_CONTACT_EMAIL）で指定した環境変数/Secretから
    # 連絡先メールを取得してUAを組み立てる。コードへ個人メールを固定しない。
    http_cfg = config.get("http", {})
    contact_email = os.environ.get(http_cfg.get("contact_email_env", "DATA_TANK_CONTACT_EMAIL"), "")
    user_agent = build_user_agent(
        name=http_cfg.get("user_agent_name", "ArticleIntelligenceDataTank"),
        contact_email=contact_email,
    )
    if not contact_email:
        print("::warning:: 連絡先メール(DATA_TANK_CONTACT_EMAIL)が未設定です。SEC/EDGАР等の"
              "連絡先付きUA必須ソースは403になる可能性があります（他ソースの取得は継続）。")

    # sources_file は config ファイルのあるディレクトリを基準に解決する。
    config_dir = Path(args.config).resolve().parent
    all_sources = load_sources(config, base_dir=config_dir)
    live_sources = enabled_sources(all_sources)

    if args.verify_candidates:
        # 未有効の候補（disabled/pending含む）だけを検証する（§16-A disabled再検証・新規追加確認）。
        candidates = [s for s in all_sources if not s.get("enabled")]
        if not candidates:
            print("未有効の候補ソースはありません。")
            return 0
        print("【候補ソースの到達性確認】OK/304 のものは enabled: true にして導入してください。\n")
        return _verify(candidates, timeout, retry, user_agent=user_agent)

    if args.verify:
        if not live_sources:
            print("enabled なソースがありません（config/sources.yaml を確認してください）。")
            return 0
        return _verify(live_sources, timeout, retry, user_agent=user_agent)

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
    # 実行間の増分取得・重複排除を成立させる（§5 索引の永続性）。
    # 再構築対象は retention 窓（retention_days）内のシャードに限定し、いずれ
    # 削除される古いシャードまで毎回読み込む無駄を省く（増加に伴う遅延を抑える）。
    # 索引はシャード(=source of truth)から再構築可能な派生データなので、消失しても
    # 記事は失われない。再構築の件数・所要秒数は run 統計・Summary に記録する。
    retention_days_cfg = tank_cfg.get("retention_days")
    rebuild_from = None
    if retention_days_cfg is not None:
        rebuild_from = (now - timedelta(days=int(retention_days_cfg))).astimezone(_JST).strftime("%Y-%m-%d")
    index_rebuilt = False
    index_rebuilt_count = 0
    index_rebuild_seconds = 0.0
    if index.count() == 0:
        import time as _time
        _t0 = _time.monotonic()
        index_rebuilt_count = rebuild_index_from_store(store, index, date_from=rebuild_from)
        index_rebuild_seconds = _time.monotonic() - _t0
        index_rebuilt = index_rebuilt_count > 0
        if index_rebuilt:
            print(f"SQLite索引が空のため、シャードから {index_rebuilt_count} 件を "
                  f"{index_rebuild_seconds:.2f}秒で再構築しました"
                  f"{'（retention窓: ' + rebuild_from + '以降）' if rebuild_from else ''}。")

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
            max_workers=max_fetch_workers, per_host_max=per_host_max,
            user_agent=user_agent,
        )
        store.write_manifest()

    # 保持期間（retention）: article_tank.retention_days（既定null=無期限）より古い
    # シャード・索引行を削除し、ストアの無制限な肥大化を防ぐ（§ retention）。
    # --verifyでは到達しない・--dry-runでも「取得・保存」の一部として実行する。
    retention_days = retention_days_cfg
    retention_deleted_shards = 0
    if retention_days is not None:
        cutoff_date = (now - timedelta(days=int(retention_days))).astimezone(_JST).strftime("%Y-%m-%d")
        purged_dates = store.purge_shards_before(cutoff_date)
        if purged_dates:
            retention_deleted_shards = len(purged_dates)
            purged_rows = index.delete_before(cutoff_date)
            store.write_manifest()
            print(
                f"保持期間（{retention_days}日）を超えたシャード{len(purged_dates)}件"
                f"（{purged_dates[0]}〜{purged_dates[-1]}）・索引{purged_rows}件を削除しました。"
                f" ※日付品質ガードにより、未来/超過去の異常日付は fetched_at へ補正済みのため、"
                f"最近の記事が古いシャードへ紛れて誤削除されることはありません。"
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

    # Source偏重の観測（§9）。保存は全件維持し、ここでは集中度の「表示」のみ行う。
    balance_cfg = config.get("source_balance", {})
    concentration = source_concentration(
        source_results,
        warning_share=float(balance_cfg.get("warning_share", 0.35)),
        critical_share=float(balance_cfg.get("critical_share", 0.60)),
    )
    # Coverage指標（Phase 3 §13, §15）: enabledソースのTier1比率・日本比率・地域/カテゴリ分布。
    from tank.source_portfolio import coverage_metrics  # noqa: E402
    coverage = coverage_metrics(all_sources, enabled_only=True)

    completed = datetime.now(timezone.utc)

    # 統計を保存（Package生成前でも必ず残す）
    def _persist_stats(package_items: int, package_size: int, run_status: str,
                       pkg_distribution: Optional[dict] = None) -> dict:
        stats = build_run_stats(
            run_id=run_id, started_at=now, completed_at=completed,
            configured_sources=len(all_sources), enabled_sources=enabled_count,
            source_results=source_results, total_tank_articles=index.count(),
            package_items=package_items, package_size=package_size,
            clusters_created=clusters_created, clusters_updated=clusters_updated,
            run_status=run_status,
            index_rebuilt=index_rebuilt, index_rebuilt_count=index_rebuilt_count,
            index_rebuild_seconds=index_rebuild_seconds,
            retention_deleted_shards=retention_deleted_shards,
            quarantine_count=quarantine_count,
            concentration=concentration, package_source_distribution=pkg_distribution or {},
            coverage=coverage,
        )
        save_run_stats(str(stats_dir), stats)
        return stats

    if args.dry_run:
        run_status = "degraded" if failed > 0 else "healthy"
        stats = _persist_stats(0, 0, run_status)
        print(json.dumps({"run_stats": stats}, ensure_ascii=False, indent=2))
        print(f"RUN STATUS: {run_status.upper()}（--dry-run のため配信Package生成はスキップ）")
        index.close()
        return 0  # dry-run はコマンド成功。exit 2 は使わない。

    # §4/§10: 全ソース失敗時は既存Packageを空Packageで上書きしない（既存Packageを保護）。
    #   - 既存の有効なPackageがある → degraded / exit 0（Data Tankとして利用可能・維持）。
    #   - 新旧どちらのPackageも無い    → failed  / exit 1（致命状態）。
    if all_failed:
        has_existing = _existing_package_is_valid(published_dir)
        run_status = "degraded" if has_existing else "failed"
        stats = _persist_stats(0, 0, run_status)
        print(json.dumps({"run_stats": stats}, ensure_ascii=False, indent=2))
        if has_existing:
            print("RUN STATUS: DEGRADED（全ソース取得失敗。既存の有効なPackageを維持しました＝"
                  "Data Tankとして利用可能。exit 0）")
        else:
            print("RUN STATUS: FAILED（全ソース取得失敗かつ既存の有効Packageなし。exit 1）")
        index.close()
        return resolve_exit_code(run_status)

    # 実記事から配信Packageを生成（hot window の記事＋本 runのクラスタ）。
    # 候補・Package選定段階では diversity.select_diverse が同一ソースの占有率に
    # 上限を課す（§9: 偏重制御は保存段階でなく選定段階で行う）。
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
            "max_published_source_share": float(balance_cfg.get("max_published_share", 0.20)),
        },
    )
    manifest = publish_package(package, str(published_dir))
    publication_ok = manifest.get("publication_status") == "success"
    pkg_dist = package_source_distribution(package.get("hot_articles", []))

    run_status = compute_run_status(
        enabled_count=enabled_count, failed_count=failed, unchanged_count=unchanged,
        success_count=success, all_failed=all_failed,
        publication_ok=publication_ok, package_published=True,
    )

    package_items = sum(manifest.get("item_counts", {}).values()) if manifest.get("item_counts") else 0
    stats = _persist_stats(package_items, manifest.get("compressed_size", 0), run_status, pkg_dist)

    print(json.dumps({"run_stats": stats, "publication_manifest": manifest}, ensure_ascii=False, indent=2))
    if concentration.get("concentration_status") in ("warning", "critical"):
        print(f"::warning:: Source偏重: {concentration['top_source']} が新規の "
              f"{concentration['top_source_share']*100:.0f}% を占有"
              f"（{concentration['concentration_status']}）。保存は全件維持、Packageは選定段階で分散化。")
    print(f"RUN STATUS: {run_status.upper()}"
          f"（publication={'success' if publication_ok else 'failed'} / "
          f"失敗ソース={failed} / 新着={success}）")
    index.close()

    return resolve_exit_code(run_status)


if __name__ == "__main__":
    raise SystemExit(main())
