"""§19, §30-24: retrieval score。記事件数を直接の主要加点にしないことを確認する。"""
from datetime import datetime, timezone

from tank.scoring import (
    compute_retrieval_score,
    freshness_score,
    independent_source_confirmation_score,
    market_reaction_score,
)


def test_freshness_decays_over_time():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    fresh = freshness_score(now.isoformat(), now)
    old = freshness_score(datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc).isoformat(), now)
    assert fresh > old
    assert fresh == 1.0 or fresh > 0.9


def test_market_reaction_score_requires_actual_reaction():
    assert market_reaction_score(has_reaction=False) == 0.0
    assert market_reaction_score(has_reaction=True, reaction_magnitude=0.8) >= 0.3


def test_independent_source_confirmation_caps_at_limit():
    assert independent_source_confirmation_score(1, cap=4) == 0.25
    assert independent_source_confirmation_score(10, cap=4) == 1.0


def test_article_count_is_not_a_direct_score_input():
    """§19: 15件のAI記事（市場反応なし）より、2件の中東記事（資産横断反応あり）の方が
    retrieval_score が高くなるべき（§18の具体例）。件数を主要加点に使うと失敗する。"""
    ai_many_low_reaction = compute_retrieval_score(
        relevance=0.6, market_reaction=market_reaction_score(False), freshness=0.8,
        source_trust=0.6, urgency=0.3, structural=0.3,
        independent_source_confirmation=independent_source_confirmation_score(15),  # 大量件数
    )
    geo_few_high_reaction = compute_retrieval_score(
        relevance=0.7, market_reaction=market_reaction_score(True, 0.9), freshness=0.7,
        source_trust=0.8, urgency=0.8, structural=0.7,
        independent_source_confirmation=independent_source_confirmation_score(2),  # 少数件数
    )
    assert geo_few_high_reaction > ai_many_low_reaction


def test_weights_sum_to_one():
    from tank.scoring import RETRIEVAL_WEIGHTS
    assert abs(sum(RETRIEVAL_WEIGHTS.values()) - 1.0) < 1e-9
