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
