"""Source偏重（source concentration）の観測（Production Stabilization §9）。

1ソースが新規記事の大半を占めると、Published Package やドライバー選定が
その媒体の論調に偏る。ただし **保存段階では全件保持** し（§9: 保存記事を削除しない）、
偏重の制御は候補・Package選定段階でのみ行う（diversity.select_diverse の
max_source_share がその実装）。本モジュールは「観測」に徹し、

  - top_source / top_source_new_count / top_source_share
  - concentration_status（ok / warning / critical）
  - package_source_distribution（配信物のソース別件数）

を算出して run統計・Summaryへ渡す。件数だけでソースを低品質扱いしない（§9）。
"""
from __future__ import annotations

from typing import Dict, List

DEFAULT_WARNING_SHARE = 0.35
DEFAULT_CRITICAL_SHARE = 0.60


def source_concentration(source_results: List[dict],
                         warning_share: float = DEFAULT_WARNING_SHARE,
                         critical_share: float = DEFAULT_CRITICAL_SHARE) -> dict:
    """今回runの新規記事数からソース集中度を算出する（保存はしない・観測のみ）。"""
    new_by_source: Dict[str, int] = {}
    for r in source_results:
        new = int(r.get("new", 0))
        if new > 0:
            new_by_source[r.get("source", "unknown")] = new_by_source.get(r.get("source", "unknown"), 0) + new
    total_new = sum(new_by_source.values())
    if total_new == 0:
        return {"top_source": "", "top_source_new_count": 0, "top_source_share": 0.0,
                "concentration_status": "ok", "total_new": 0}
    top_source, top_count = max(new_by_source.items(), key=lambda kv: kv[1])
    share = round(top_count / total_new, 4)
    if share >= critical_share:
        status = "critical"
    elif share >= warning_share:
        status = "warning"
    else:
        status = "ok"
    return {"top_source": top_source, "top_source_new_count": top_count,
            "top_source_share": share, "concentration_status": status, "total_new": total_new}


def package_source_distribution(hot_articles: List[dict]) -> Dict[str, int]:
    """配信Packageの hot_articles をソース別件数に集計する（公開情報のみ・本文非参照）。"""
    dist: Dict[str, int] = {}
    for a in hot_articles:
        src = a.get("source", "") or "unknown"
        dist[src] = dist.get(src, 0) + 1
    return dict(sorted(dist.items(), key=lambda kv: -kv[1]))
