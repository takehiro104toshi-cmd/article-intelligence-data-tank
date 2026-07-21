#!/usr/bin/env python3
"""Rashinban Private Insight Vault — 分析パイプラインのCLIエントリーポイント。

本番: Cloudflare Worker の queue から未分析記事を取得→分析→結果を返す。
      本文はこのプロセスのメモリのみを通過し、リポジトリ・ログへ一切出力しない
      （出力するのは件数とIDだけ）。

    INSIGHT_API_URL=https://<worker>/api/private-insight \
    INSIGHT_API_TOKEN=... \
    python scripts/run_private_insight_analysis.py

ローカル開発: --local で data/private_insights/（.gitignore済み）を対象に、
    保存済みで未分析のレコードを分析する。

    python scripts/run_private_insight_analysis.py --local

Secretが未設定の場合は静かに終了する（既存パイプラインを止めない）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tank.private_insight import (  # noqa: E402
    LocalPrivateInsightStore, analyze_record, sync_and_analyze_from_worker,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Private Insight analysis runner")
    parser.add_argument("--local", action="store_true", help="ローカルstoreの未分析レコードを処理する")
    parser.add_argument("--max-items", type=int, default=20)
    args = parser.parse_args(argv)

    if args.local:
        store = LocalPrivateInsightStore(str(ROOT / "data" / "private_insights"))
        pending = [r for r in store.list_records() if r.status in ("stored", "queued", "needs_review", "failed")]
        done = 0
        for rec in pending[: args.max_items]:
            if analyze_record(store, rec.private_article_id):
                done += 1
        print(f"local: pending={len(pending)} analyzed={done}")
        return 0

    api_url = os.environ.get("INSIGHT_API_URL", "").rstrip("/")
    token = os.environ.get("INSIGHT_API_TOKEN", "")
    if not api_url or not token:
        print("INSIGHT_API_URL / INSIGHT_API_TOKEN が未設定のためスキップします（正常終了）。")
        return 0

    result = sync_and_analyze_from_worker(api_url, token, config={
        "model": os.environ.get("PRIVATE_INSIGHT_MODEL", ""),
        "fallback_to_rule_based": True,
    }, max_items=args.max_items)
    # 本文・タイトル等は出力しない（件数のみ）
    print(f"worker sync: fetched={result['fetched']} analyzed={result['analyzed']} failed={result['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
