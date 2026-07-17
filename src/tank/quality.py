"""Data Tank Status / Quality 監視（§24, §7 tank_status/quality）。

偏り監視（bias monitoring, §17）: AI・半導体の比率を「表示」するだけで、
選定ロジック側でAI・半導体を優遇するコードパスは無い（classify.py/diversity.py参照）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from .index import ArticleIndex


def build_tank_status(index: ArticleIndex, now: datetime, quarantine_count: int = 0) -> dict:
    total = index.count()
    since = (now - timedelta(hours=24)).isoformat()
    new_24h = index.count_since(since)
    return {
        "total_articles": total,
        "new_articles_24h": new_24h,
        "source_count": index.source_domain_count(),
        "latest_article_at": index.latest_published_at() or "",
        "storage_status": "degraded" if quarantine_count > 0 else "healthy",
    }


def build_quality_metrics(published_articles: List, index: ArticleIndex, reaction_coverage_count: int) -> dict:
    """Published Packageの `quality` ブロック（§7）。件数だけでなく分散も見る。"""
    total_published = len(published_articles) or 1
    unique_sources = len({getattr(a, "source_domain", "") for a in published_articles})
    unique_themes = len({(a.themes[0] if getattr(a, "themes", None) else a.primary_category) for a in published_articles})

    ai_semi = sum(
        1 for a in published_articles
        if a.primary_category in ("ai", "semiconductor") or "ai" in a.themes or "semiconductor" in a.themes
    )

    diversity_score = round(min(1.0, (unique_sources / total_published) * 0.5 + (unique_themes / total_published) * 0.5), 4)
    freshness_values = [getattr(a, "freshness_score", 0.0) for a in published_articles]
    source_trust_values = [getattr(a, "source_score", 0.0) or getattr(a, "source_trust", 0.0) for a in published_articles]

    return {
        "freshness_score": round(sum(freshness_values) / total_published, 4) if freshness_values else 0.0,
        "diversity_score": diversity_score,
        "source_quality_score": round(sum(source_trust_values) / total_published, 4) if source_trust_values else 0.0,
        "market_reaction_coverage": round(reaction_coverage_count / total_published, 4) if published_articles else 0.0,
        "ai_semiconductor_share": round(ai_semi / total_published, 4) if published_articles else 0.0,
    }
