"""§30-11/12: exact duplicate / syndicated duplicate。"""
from tank.dedup import is_exact_duplicate, is_syndicated_duplicate, pick_representative
from tests.factories import make_article


def test_exact_duplicate_same_canonical_url():
    a = make_article(url="https://example.com/1", title="速報：金利発表")
    b = make_article(url="https://example.com/1", title="速報：金利発表")
    assert is_exact_duplicate(a, b) is True


def test_not_duplicate_when_url_and_content_differ():
    a = make_article(url="https://example.com/1", title="速報：金利発表")
    b = make_article(url="https://example.com/2", title="別のニュースです")
    assert is_exact_duplicate(a, b) is False
    assert is_syndicated_duplicate(a, b) is False


def test_syndicated_duplicate_same_title_different_source():
    # 見出しは同じ（配信元が転電）だが、本文（description）は配信社ごとに異なる文面
    a = make_article(url="https://source-a.com/1", source_domain="source-a.com",
                     title="米CPIが市場予想を上回る", description="A社independent配信の解説記事です。")
    b = make_article(url="https://source-b.com/9", source_domain="source-b.com",
                     title="米CPIが市場予想を上回る", description="B社が独自に追加した分析コメントです。")
    assert is_syndicated_duplicate(a, b) is True
    assert is_exact_duplicate(a, b) is False


def test_representative_prefers_higher_source_trust():
    a = make_article(url="https://a.com/1", source_trust=0.5, title="同じ話題です")
    b = make_article(url="https://b.com/1", source_trust=0.9, title="同じ話題です")
    rep = pick_representative([a, b])
    assert rep.source_trust == 0.9
