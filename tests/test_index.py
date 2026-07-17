"""§30-17〜23: 横断検索（日付/カテゴリ/国/地政学エンティティ/企業/資源/ソース信頼度）。"""
from datetime import datetime, timezone

from tank.index import ArticleIndex
from tests.factories import make_article


def _build_index(tmp_path):
    idx = ArticleIndex(str(tmp_path / "index.sqlite"))
    articles = [
        make_article(url="https://a.example/1", category="oil", countries=["Iran"],
                    commodities=["oil"], geo_entities=["Iran"],
                    published_at=datetime(2026, 7, 10, tzinfo=timezone.utc)),
        make_article(url="https://b.example/1", category="semiconductor", countries=["Taiwan"],
                    companies=["TSMC"], published_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
                    source_trust=0.9),
        make_article(url="https://c.example/1", category="earnings", countries=["Japan"],
                    companies=["Toyota"], published_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
                    source_trust=0.3),
    ]
    idx.upsert_articles(articles)
    return idx, articles


def test_search_by_category(tmp_path):
    idx, articles = _build_index(tmp_path)
    result = idx.search(category="oil")
    assert result == [articles[0].article_id]


def test_search_by_country(tmp_path):
    idx, articles = _build_index(tmp_path)
    result = idx.search(country="Taiwan")
    assert result == [articles[1].article_id]


def test_search_by_geopolitical_entity(tmp_path):
    idx, articles = _build_index(tmp_path)
    result = idx.search(geopolitical_entity="Iran")
    assert result == [articles[0].article_id]


def test_search_by_company(tmp_path):
    idx, articles = _build_index(tmp_path)
    result = idx.search(company="TSMC")
    assert result == [articles[1].article_id]


def test_search_by_commodity(tmp_path):
    idx, articles = _build_index(tmp_path)
    result = idx.search(commodity="oil")
    assert result == [articles[0].article_id]


def test_search_by_date_range(tmp_path):
    idx, articles = _build_index(tmp_path)
    result = idx.search(date_from="2026-07-14T00:00:00+00:00")
    ids = set(result)
    assert articles[1].article_id in ids and articles[2].article_id in ids
    assert articles[0].article_id not in ids


def test_search_by_min_source_trust(tmp_path):
    idx, articles = _build_index(tmp_path)
    result = idx.search(min_source_trust=0.8)
    assert result == [articles[1].article_id]


def test_count_and_source_domain_count(tmp_path):
    idx, articles = _build_index(tmp_path)
    assert idx.count() == 3
    assert idx.source_domain_count() == 3
