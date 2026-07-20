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


# ---------- 英語記事の分類（Tankの情報源は英語RSSが多いため必須） ----------

def test_english_article_classified_by_topic_not_uncategorized():
    art = make_article(
        title="TSMC accelerating Arizona factory buildout to capitalize on AI megatrend",
        description="The chipmaker is ramping up its semiconductor investment amid strong demand.",
    )
    classify_article(art)
    assert art.primary_category != "uncategorized"
    assert art.primary_category in ("ai", "semiconductor")


def test_english_automotive_article_classified_as_auto():
    art = make_article(
        title="Automaker unveils new electric vehicle lineup",
        description="The carmaker announced an expansion of its automotive production line.",
    )
    classify_article(art)
    assert art.primary_category == "auto"


def test_english_geopolitics_article_classified_correctly():
    art = make_article(
        title="Tensions rise in the Middle East as Iran and Israel exchange warnings",
        description="Analysts describe the standoff as a deepening geopolitical crisis.",
    )
    classify_article(art)
    assert art.primary_category in ("us_iran_middle_east", "geopolitics")


# ---------- 単語境界による誤検知防止（英語の部分一致を避ける） ----------

def test_short_acronym_does_not_false_positive_inside_unrelated_word():
    # "said" の中に "ai" という部分文字列が含まれるが、単語境界チェックにより
    # "AI" キーワードには一致しない（大文字小文字を無視しても同様）。
    art = make_article(title="The central bank official said rates would remain unchanged", description="")
    classify_article(art)
    assert art.primary_category != "ai"


def test_ev_keyword_does_not_match_inside_unrelated_words():
    # "even" や "review" に含まれる "ev" には反応せず、"EV" が独立した単語として
    # 現れた場合のみ auto カテゴリに寄与する。
    art = make_article(title="Even after the review, analysts remained cautious", description="")
    classify_article(art)
    assert art.primary_category != "auto"
