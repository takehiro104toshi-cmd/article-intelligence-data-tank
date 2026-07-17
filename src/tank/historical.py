"""Historical Context 検索（§23）。

現在のイベントに関連する過去記事を、共有エンティティ・カテゴリの重なりと
新しさから軽量にスコアリングして検索する。全文は使わず、保存済みの
public_excerpt から短い context だけを切り出す（捏造しない・全文を渡さない）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from .models import Article


def _entities(article: Article) -> set:
    return set(article.countries) | set(article.companies) | set(article.commodities) | set(article.geopolitical_entities)


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_historical_matches(
    current: Article,
    past_articles: List[Article],
    now: datetime,
    max_results: int = 20,
    lookback_days: int = 730,
) -> List[dict]:
    """current と関連する過去記事を軽量情報（§23の項目のみ）で返す。"""
    cur_entities = _entities(current)
    if not cur_entities:
        return []
    cur_dt = _parse_dt(current.published_at_utc) or now

    scored = []
    for past in past_articles:
        if past.article_id == current.article_id:
            continue
        past_dt = _parse_dt(past.published_at_utc)
        if past_dt is None:
            continue
        age_days = (cur_dt - past_dt).total_seconds() / 86400.0
        if age_days <= 0 or age_days > lookback_days:
            continue
        shared = cur_entities & _entities(past)
        if not shared:
            continue
        entity_overlap = len(shared) / max(1, len(cur_entities | _entities(past)))
        recency = max(0.0, 1.0 - (age_days / lookback_days))
        relevance = round(min(1.0, entity_overlap * 0.7 + recency * 0.3), 4)
        scored.append((relevance, past, shared))

    scored.sort(key=lambda x: -x[0])
    out = []
    for relevance, past, shared in scored[:max_results]:
        out.append({
            "historical_event_id": past.event_cluster_id or past.article_id,
            "title": past.title_original,
            "date": past.published_at_jst or past.published_at_utc,
            "relevance_score": relevance,
            "related_entities": sorted(shared),
            "related_assets": list(past.affected_assets),
            "observed_market_reaction": past.priced_in_status,
            "short_context": (past.public_excerpt or past.description or "")[:140],
            "source_count": 1,
        })
    return out
