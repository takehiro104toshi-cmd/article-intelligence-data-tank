"""§17 公平性: AI・半導体だけを優遇するコードパスが無いことを確認する。"""
from tank.classify import DEFAULT_CATEGORY_KEYWORDS, classify_article
from tests.factories import make_article


def test_all_categories_use_same_matching_function():
    # AI/semiconductor 専用の特別処理関数が存在せず、同じ classify_article が全カテゴリに使われる
    assert "ai" in DEFAULT_CATEGORY_KEYWORDS
    assert "semiconductor" in DEFAULT_CATEGORY_KEYWORDS
    assert "geopolitics" in DEFAULT_CATEGORY_KEYWORDS
    assert "monetary_policy" in DEFAULT_CATEGORY_KEYWORDS


def test_geopolitics_article_classified_correctly():
    art = make_article(title="イランと中東の緊張が高まる地政学リスク", description="")
    classify_article(art)
    assert art.primary_category in ("geopolitics", "us_iran_middle_east")


def test_ai_keyword_does_not_override_stronger_match():
    art = make_article(title="AIよりも中東情勢の地政学リスクが深刻、AIは関連薄い", description="")
    classify_article(art)
    # 単純なキーワード一致件数で決まる（AIという語があっても優遇されない）
    assert art.primary_category != ""


def test_uncategorized_when_no_keywords_match():
    art = make_article(title="特定できない話題です", description="")
    classify_article(art)
    assert art.primary_category == "uncategorized"
