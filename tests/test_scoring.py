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


# ---------- v0.5.0: score_article_signals（記事レベルのimportance等の算出） ----------

def _scored_article(title, category, themes=None, trust=0.5):
    from tank.scoring import score_article_signals
    from tests.factories import make_article

    art = make_article(title=title, source_trust=trust)
    art.primary_category = category
    art.themes = themes if themes is not None else ([category] if category != "uncategorized" else [])
    score_article_signals(art)
    return art


def test_monetary_policy_scores_higher_than_uncategorized():
    fed = _scored_article("FRB signals rate cut", "monetary_policy", trust=0.98)
    misc = _scored_article("Local festival draws crowds", "uncategorized", trust=0.5)
    assert fed.importance_score > misc.importance_score
    assert fed.market_impact_score > misc.market_impact_score
    assert fed.structural_score == 1.0
    assert misc.structural_score == 0.0


def test_urgent_keyword_raises_urgency_and_impact():
    calm = _scored_article("Oil market weekly review", "oil")
    urgent = _scored_article("Oil prices plunge after supply shock", "oil")
    assert urgent.urgency_score > calm.urgency_score
    assert urgent.market_impact_score > calm.market_impact_score


def test_scores_are_bounded_zero_to_one():
    art = _scored_article("緊急 utage rate hike crash plunge", "monetary_policy",
                          themes=["monetary_policy", "rates", "fx", "inflation"], trust=1.0)
    for value in (art.importance_score, art.market_impact_score, art.urgency_score, art.structural_score):
        assert 0.0 <= value <= 1.0


def test_theme_summary_excludes_uncategorized():
    from tank.publication import build_theme_summary
    from tests.factories import make_article

    a1 = make_article(url="https://x/1")
    a1.themes = ["ai"]
    a2 = make_article(url="https://x/2")
    a2.themes = []
    a2.primary_category = "uncategorized"
    summary = build_theme_summary([a1, a2], limit=10)
    themes = [e["theme"] for e in summary]
    assert "ai" in themes
    assert "uncategorized" not in themes
