"""§21, §23: Market Reaction Storeの新着トラッキング開始と、Historical Context検索。"""
from datetime import datetime, timedelta, timezone

from tank.historical import find_historical_matches
from tank.market_reaction import MarketReactionStore
from tests.factories import make_article


def test_reaction_tracking_starts_for_new_event_without_backfill(tmp_path):
    store = MarketReactionStore(str(tmp_path / "reactions.json"))
    stub = store.start_tracking("evc_1")
    assert all(v is None for target in stub.values() for v in target.values())
    assert store.has_any_reaction("evc_1") is False


def test_recorded_reaction_is_detected(tmp_path):
    store = MarketReactionStore(str(tmp_path / "reactions.json"))
    store.start_tracking("evc_1")
    store.record_reaction("evc_1", "wti", "1h", 2.5)
    assert store.has_any_reaction("evc_1") is True
    assert store.reaction_magnitude("evc_1") > 0


def test_historical_matches_use_only_lightweight_fields():
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    current = make_article(url="https://cur.example/1", title="台湾を巡る緊張再燃",
                           geo_entities=["Taiwan", "China"], published_at=now)
    past = make_article(
        url="https://past.example/1", title="過去の台湾海峡危機の記録", geo_entities=["Taiwan", "China"],
        published_at=now - timedelta(days=200), description="過去の全文がここに入る" * 50,
    )
    matches = find_historical_matches(current, [past], now)
    assert len(matches) == 1
    m = matches[0]
    assert set(m.keys()) == {
        "historical_event_id", "title", "date", "relevance_score", "related_entities",
        "related_assets", "observed_market_reaction", "short_context", "source_count",
    }
    assert len(m["short_context"]) <= 140  # 全文は渡さない


def test_no_shared_entities_means_no_match():
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    current = make_article(title="無関係な話題", geo_entities=[], published_at=now)
    past = make_article(title="別の話題", geo_entities=["Taiwan"], published_at=now - timedelta(days=10))
    assert find_historical_matches(current, [past], now) == []
