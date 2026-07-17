"""テスト用の Article/EventCluster 生成ヘルパー。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tank.dedup import compute_hashes
from tank.models import Article
from tank.url_normalize import source_domain_of


def make_article(
    title="サンプル記事",
    url="https://example.com/a1",
    source_domain=None,
    source_name="Example",
    source_trust=0.7,
    category="geopolitics",
    countries=None,
    companies=None,
    commodities=None,
    geo_entities=None,
    themes=None,
    published_at=None,
    importance=0.5,
    market_impact=0.5,
    urgency=0.5,
    structural=0.5,
    description="サンプル説明文です。",
) -> Article:
    published_at = published_at or datetime.now(timezone.utc)
    resolved_domain = source_domain if source_domain is not None else source_domain_of(url)
    art = Article(
        canonical_url=url,
        normalized_url=url,
        source_domain=resolved_domain,
        source_name=source_name,
        source_trust=source_trust,
        title_original=title,
        description=description,
        public_excerpt=description[:100],
        primary_category=category,
        themes=themes or [category],
        countries=countries or [],
        companies=companies or [],
        commodities=commodities or [],
        geopolitical_entities=geo_entities or [],
        published_at_utc=published_at.isoformat(),
        published_at_jst=(published_at.astimezone(timezone(timedelta(hours=9)))).strftime("%Y-%m-%d"),
        importance_score=importance,
        market_impact_score=market_impact,
        urgency_score=urgency,
        structural_score=structural,
    )
    art.article_id = "art_" + compute_hashes(art).canonical_hash[:24]
    compute_hashes(art)
    return art
