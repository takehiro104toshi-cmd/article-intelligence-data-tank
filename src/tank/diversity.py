"""多様性制御（§20, §30-25〜28）。

Published Package が同一テーマ・同一ソース・同一event clusterで埋まらないように、
retrieval_score の高い順に貪欲選択しつつ、以下の上限を超えないよう制御する。

  - 同一sourceは上限 max_source_share（既定25%）
  - 同一themeは上限 max_theme_share（既定35%）
  - 同一event clusterは上限 max_event_cluster_articles（既定3件）

重要性の低い記事を無理に採用して見かけだけ多様にすることはしない
（候補が足りなければ target_count 未満のまま返す）。
"""
from __future__ import annotations

from typing import List

from .models import Article


def select_diverse(
    candidates: List[Article],
    target_count: int,
    max_source_share: float = 0.25,
    max_theme_share: float = 0.35,
    max_event_cluster_articles: int = 3,
) -> List[Article]:
    """retrieval_score（呼び出し側で candidate に一時付与した _retrieval_score属性、
    無ければ importance_score+market_impact_score）降順に貪欲選択する。
    """
    def sort_key(a: Article):
        score = getattr(a, "_retrieval_score", None)
        if score is None:
            score = a.importance_score + a.market_impact_score
        return -score

    ordered = sorted(candidates, key=sort_key)
    selected: List[Article] = []
    source_counts: dict = {}
    theme_counts: dict = {}
    cluster_counts: dict = {}

    for art in ordered:
        if len(selected) >= target_count:
            break
        source_cap = max(1, int(target_count * max_source_share))
        theme_cap = max(1, int(target_count * max_theme_share))

        if source_counts.get(art.source_domain, 0) >= source_cap:
            continue
        primary_theme = art.themes[0] if art.themes else art.primary_category
        if theme_counts.get(primary_theme, 0) >= theme_cap:
            continue
        if art.event_cluster_id and cluster_counts.get(art.event_cluster_id, 0) >= max_event_cluster_articles:
            continue

        selected.append(art)
        source_counts[art.source_domain] = source_counts.get(art.source_domain, 0) + 1
        theme_counts[primary_theme] = theme_counts.get(primary_theme, 0) + 1
        if art.event_cluster_id:
            cluster_counts[art.event_cluster_id] = cluster_counts.get(art.event_cluster_id, 0) + 1

    return selected
