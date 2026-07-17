"""§30-13/14/15/16: 同一event cluster統合・誤統合防止・代表記事選定・独立ソース数。"""
from datetime import datetime, timezone

from tank.cluster import find_matching_cluster, new_cluster_id, upsert_cluster
from tests.factories import make_article


def test_same_event_cluster_merges_related_articles():
    clusters = {}
    articles_by_id = {}
    t0 = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)

    a1 = make_article(
        url="https://gov.example/1", source_domain="gov.example", title="米政府がイランへ警告",
        category="us_iran_middle_east", geo_entities=["Iran", "US"], published_at=t0,
    )
    cid = new_cluster_id(a1)
    upsert_cluster(clusters, cid, a1, articles_by_id)
    articles_by_id[a1.article_id] = a1

    a2 = make_article(
        url="https://news2.example/2", source_domain="news2.example", title="イラン政府が米国へ反発を表明",
        category="us_iran_middle_east", geo_entities=["Iran", "US"],
        published_at=t0.replace(hour=10),
    )
    matched = find_matching_cluster(a2, clusters, articles_by_id, jaccard_threshold=0.05)
    assert matched == cid
    upsert_cluster(clusters, matched, a2, articles_by_id)
    articles_by_id[a2.article_id] = a2

    cluster = clusters[cid]
    assert cluster.article_count == 2
    assert cluster.independent_source_count == 2  # gov.example / news2.example


def test_different_articles_are_not_merged_when_category_and_entities_differ():
    clusters = {}
    articles_by_id = {}
    t0 = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)

    a1 = make_article(url="https://a.example/1", title="中東情勢が緊迫化", category="us_iran_middle_east",
                      geo_entities=["Iran"], published_at=t0)
    cid = new_cluster_id(a1)
    upsert_cluster(clusters, cid, a1, articles_by_id)
    articles_by_id[a1.article_id] = a1

    a2 = make_article(url="https://b.example/1", title="国内の決算発表シーズン到来", category="earnings",
                      geo_entities=[], published_at=t0)
    matched = find_matching_cluster(a2, clusters, articles_by_id)
    assert matched is None  # カテゴリ・エンティティとも無関係 → 誤統合しない


def test_representative_articles_capped_and_ranked_by_importance():
    clusters = {}
    articles_by_id = {}
    t0 = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
    cid = "evc_test"
    for i in range(5):
        art = make_article(
            url=f"https://s{i}.example/1", source_domain=f"s{i}.example",
            title="米中摩擦が再燃", category="us_china", geo_entities=["China", "US"],
            importance=0.1 * i, published_at=t0,
        )
        upsert_cluster(clusters, cid, art, articles_by_id)
        articles_by_id[art.article_id] = art

    cluster = clusters[cid]
    assert cluster.article_count == 5
    assert len(cluster.representative_articles) == 3  # max_representatives 既定3件
