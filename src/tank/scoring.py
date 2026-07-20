"""Retrieval Score（§19）と各種サブスコア。

retrieval_score = event/query relevance 25% + market reaction 25% + freshness 15%
                 + source trust 10% + urgency 10% + structural importance 10%
                 + independent source confirmation 5%

記事件数（raw count）は直接の主要加点にしない（§19, §30-24 のテスト対象）。
independent_source_confirmation は「重複しないソースドメイン数」であり、
同一ソースからの水増し記事では上がらない。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

RETRIEVAL_WEIGHTS = {
    "relevance": 0.25,
    "market_reaction": 0.25,
    "freshness": 0.15,
    "source_trust": 0.10,
    "urgency": 0.10,
    "structural": 0.10,
    "independent_source_confirmation": 0.05,
}

# 構造的テーマ（政策・地政学・供給網など、単発で終わりにくいカテゴリ）。
# §17 公平性: AI・半導体だけでなく、金融政策・地政学・資源・防衛等を同列に扱う。
STRUCTURAL_CATEGORIES = {
    "monetary_policy", "fiscal_policy", "geopolitics", "diplomacy_war",
    "us_china", "us_iran_middle_east", "taiwan", "ukraine", "north_korea",
    "sanctions", "tariffs", "semiconductor", "ai", "defense",
    "electric_power", "rare_earth", "regulation",
}

# 市場価格へ直接波及しやすいカテゴリ（金利・為替・物価・エネルギー・決算等）。
HIGH_MARKET_IMPACT_CATEGORIES = {
    "monetary_policy", "rates", "fx", "inflation", "employment", "gdp",
    "oil", "natural_gas", "gold", "semiconductor", "ai", "earnings",
    "banking", "tariffs", "sanctions", "mna",
}

# 緊急性キーワード（日英）。タイトルに含まれると urgency が上がる。
URGENT_KEYWORDS = (
    "急落", "急騰", "暴落", "緊急", "破綻", "利上げ", "利下げ", "介入", "攻撃", "侵攻",
    "crash", "plunge", "surge", "emergency", "collapse", "default", "bankruptcy",
    "rate hike", "rate cut", "attack", "invasion", "strike",
)


def score_article_signals(article) -> None:
    """記事のimportance/market_impact/urgency/structuralスコアを機械的に算出して書き込む。

    分類結果（primary_category/themes）・情報源信頼度・緊急性キーワードのみから
    決定論的に導く（生成AI・外部データなし）。全カテゴリを同一式で評価するため、
    特定テーマを優遇するコードパスは存在しない（§17）。呼び出しはclassify後を前提とする。
    """
    title = article.title_original or ""
    categorized = article.primary_category not in ("", "uncategorized")

    urgent = any(kw.lower() in title.lower() if kw.isascii() else kw in title for kw in URGENT_KEYWORDS)
    article.urgency_score = 0.7 if urgent else 0.2

    if article.primary_category in HIGH_MARKET_IMPACT_CATEGORIES:
        market_impact = 0.6
    elif categorized:
        market_impact = 0.35
    else:
        market_impact = 0.1
    if urgent:
        market_impact += 0.2
    article.market_impact_score = min(1.0, market_impact)

    if article.primary_category in STRUCTURAL_CATEGORIES:
        article.structural_score = 1.0
    elif any(t in STRUCTURAL_CATEGORIES for t in (article.themes or [])):
        article.structural_score = 0.5
    else:
        article.structural_score = 0.0

    theme_breadth = min(1.0, len(article.themes or []) / 3.0)
    article.importance_score = round(min(1.0, (
        0.35 * article.market_impact_score
        + 0.25 * article.structural_score
        + 0.20 * article.source_trust
        + 0.10 * theme_breadth
        + 0.10 * article.urgency_score
    )), 4)


def freshness_score(published_at_utc: str, now: datetime, half_life_hours: float = 24.0) -> float:
    """公開からの経過時間に応じて 1.0→0.0 へ減衰するスコア（48時間でおおよそ0付近）。"""
    if not published_at_utc:
        return 0.0
    try:
        published = datetime.fromisoformat(published_at_utc.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    now_aware = now if now.tzinfo else now.replace(tzinfo=published.tzinfo)
    elapsed_hours = max(0.0, (now_aware - published).total_seconds() / 3600.0)
    if elapsed_hours <= 0:
        return 1.0
    score = 0.5 ** (elapsed_hours / half_life_hours)
    return max(0.0, min(1.0, score))


def independent_source_confirmation_score(independent_source_count: int, cap: int = 4) -> float:
    """独立ソース数（重複しないドメイン数）を 0-1 へ正規化。capを超えても頭打ち。"""
    return min(1.0, independent_source_count / cap) if cap > 0 else 0.0


def market_reaction_score(has_reaction: bool, reaction_magnitude: float = 0.0) -> float:
    """市場が実際に反応したかどうかを主軸にしたスコア（§18 Market Reaction First）。
    reaction_magnitude は 0.0-1.0 に正規化された反応の大きさ（無ければ0）。
    """
    if not has_reaction:
        return 0.0
    return max(0.3, min(1.0, reaction_magnitude))  # 反応ありなら最低0.3を保証


def compute_retrieval_score(
    relevance: float,
    market_reaction: float,
    freshness: float,
    source_trust: float,
    urgency: float,
    structural: float,
    independent_source_confirmation: float,
    weights: Optional[dict] = None,
) -> float:
    """各サブスコア（0.0-1.0）を重み付き合算し、0-100 の retrieval_score を返す。"""
    w = weights or RETRIEVAL_WEIGHTS
    total = (
        relevance * w["relevance"]
        + market_reaction * w["market_reaction"]
        + freshness * w["freshness"]
        + source_trust * w["source_trust"]
        + urgency * w["urgency"]
        + structural * w["structural"]
        + independent_source_confirmation * w["independent_source_confirmation"]
    )
    return round(max(0.0, min(1.0, total)) * 100, 2)
