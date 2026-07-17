"""§20, §30-25/26/27/28: 多様性制御（source share / theme share / event cluster上限）。"""
from tank.diversity import select_diverse
from tests.factories import make_article


def _with_score(article, score):
    article._retrieval_score = score
    return article


def test_source_share_cap_enforced():
    articles = [
        _with_score(make_article(url=f"https://same.example/{i}", source_domain="same.example",
                                 title=f"記事{i}"), 100 - i)
        for i in range(10)
    ]
    selected = select_diverse(articles, target_count=10, max_source_share=0.25)
    # 上限 = floor(10*0.25) -> max(1, 2) = 2件までしか同一sourceを選ばない
    assert sum(1 for a in selected if a.source_domain == "same.example") <= 2


def test_theme_share_cap_enforced():
    articles = [
        _with_score(make_article(url=f"https://s{i}.example/1", source_domain=f"s{i}.example",
                                 title=f"AIニュース{i}", themes=["ai"]), 100 - i)
        for i in range(10)
    ]
    selected = select_diverse(articles, target_count=10, max_theme_share=0.35)
    assert sum(1 for a in selected if a.themes and a.themes[0] == "ai") <= 3


def test_event_cluster_cap_enforced():
    articles = []
    for i in range(6):
        a = make_article(url=f"https://s{i}.example/1", source_domain=f"s{i}.example", title=f"記事{i}")
        a.event_cluster_id = "evc_shared"
        articles.append(_with_score(a, 100 - i))
    selected = select_diverse(articles, target_count=10, max_event_cluster_articles=3)
    assert sum(1 for a in selected if a.event_cluster_id == "evc_shared") <= 3


def test_does_not_pad_with_low_quality_when_candidates_insufficient():
    articles = [_with_score(make_article(url="https://only.example/1"), 90)]
    selected = select_diverse(articles, target_count=10)
    assert len(selected) == 1  # 無理に水増ししない


def test_selection_prefers_higher_retrieval_score():
    low = _with_score(make_article(url="https://a.example/1", source_domain="a.example"), 10)
    high = _with_score(make_article(url="https://b.example/1", source_domain="b.example"), 90)
    selected = select_diverse([low, high], target_count=1)
    assert selected[0] is high
