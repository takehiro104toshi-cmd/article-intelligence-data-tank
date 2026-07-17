"""§30-2/3/4: article_id安定性・URL正規化・トラッキングパラメータ除去。"""
from tank.dedup import article_id_from_canonical
from tank.url_normalize import normalize_url, source_domain_of


def test_tracking_params_removed():
    url = "https://example.com/news/1?utm_source=x&utm_medium=y&fbclid=abc&id=1"
    norm = normalize_url(url)
    assert "utm_" not in norm and "fbclid" not in norm
    assert "id=1" in norm


def test_normalization_is_case_and_slash_insensitive():
    a = normalize_url("HTTPS://Example.COM/News/1/")
    b = normalize_url("https://example.com/News/1")
    assert a == b


def test_query_param_order_does_not_affect_normalization():
    a = normalize_url("https://example.com/n?b=2&a=1")
    b = normalize_url("https://example.com/n?a=1&b=2")
    assert a == b


def test_article_id_is_stable_for_equivalent_urls():
    a = article_id_from_canonical(normalize_url("https://example.com/x?utm_source=a"))
    b = article_id_from_canonical(normalize_url("https://example.com/x?utm_source=b"))
    assert a == b  # トラッキングパラメータ違いは同一記事扱い


def test_article_id_differs_for_different_paths():
    a = article_id_from_canonical(normalize_url("https://example.com/x"))
    b = article_id_from_canonical(normalize_url("https://example.com/y"))
    assert a != b


def test_source_domain_strips_www():
    assert source_domain_of("https://www.example.com/a") == "example.com"
    assert source_domain_of("https://news.example.com/a") == "news.example.com"
