"""§30-1: Article/EventCluster/SourceCursor スキーマの必須フィールドを確認する。"""
from tank.models import Article, EventCluster, SourceCursor, new_market_reaction_stub, MARKET_REACTION_TARGETS, MARKET_REACTION_WINDOWS


def test_article_schema_has_required_fields():
    a = Article()
    required = [
        "article_id", "canonical_url", "source_domain", "source_trust",
        "title_original", "public_excerpt", "body_storage_type", "rights_classification",
        "published_at_utc", "published_at_jst", "primary_category", "themes",
        "importance_score", "market_impact_score", "urgency_score", "structural_score",
        "content_hash", "title_hash", "canonical_hash", "event_cluster_id",
        "ingestion_run_id", "classification_status", "error_flags",
    ]
    for field in required:
        assert hasattr(a, field), field


def test_article_roundtrip_dict():
    a = Article(title_original="test", canonical_url="https://x.com/1")
    d = a.to_dict()
    a2 = Article.from_dict(d)
    assert a2.title_original == "test"
    assert a2.canonical_url == "https://x.com/1"


def test_event_cluster_schema_has_required_fields():
    c = EventCluster()
    for field in ["event_cluster_id", "event_title", "category", "countries", "actors",
                  "article_count", "independent_source_count", "escalation_status",
                  "representative_articles"]:
        assert hasattr(c, field)


def test_source_cursor_schema_has_required_fields():
    c = SourceCursor()
    for field in ["source_name", "last_fetch_started_at", "last_fetch_completed_at",
                  "latest_published_at", "etag", "consecutive_failures", "next_retry_at"]:
        assert hasattr(c, field)


def test_market_reaction_stub_covers_all_targets_and_windows():
    stub = new_market_reaction_stub()
    assert set(stub.keys()) == set(MARKET_REACTION_TARGETS)
    for target in MARKET_REACTION_TARGETS:
        assert set(stub[target].keys()) == set(MARKET_REACTION_WINDOWS)
        assert all(v is None for v in stub[target].values())  # バックフィル必須ではない
