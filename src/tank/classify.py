"""キーワードベースの機械的分類（§9 分類フィールド, §17 公平性）。

生成AIは使わない。config.yaml の `categories` に列挙されたテーマを、同一の
アルゴリズムで並列に評価する（AI・半導体だけを特別扱いするコードパスは存在しない）。
一致件数が最大のカテゴリを primary_category、閾値以上一致した他カテゴリを
secondary_categories／themes とする。
"""
from __future__ import annotations

import re
from typing import Dict, List

from .models import Article

# §17 で列挙された分野を「公平に」扱うための既定キーワード（categories）。
# 日本語・英語の両方を列挙する（Tankの情報源は英語RSSが多いため、日本語のみでは
# 大半の記事がuncategorizedになってしまう。§17と同じロジックで両言語を並列に評価）。
# config.yaml で上書き・追加可能（利用者が実際のニュースソースに合わせて調整する）。
DEFAULT_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "geopolitics": ["地政学", "紛争", "軍事", "衝突", "geopolitical", "geopolitics"],
    "diplomacy_war": ["戦争", "外交", "停戦", "侵攻", "diplomacy", "ceasefire", "invasion", "warfare"],
    "us_china": ["米中", "中国", "台湾海峡", "US-China", "Sino-US", "China"],
    "us_iran_middle_east": ["イラン", "中東", "ホルムズ海峡", "イスラエル", "Iran", "Middle East", "Strait of Hormuz", "Israel"],
    "taiwan": ["台湾", "Taiwan"],
    "ukraine": ["ウクライナ", "Ukraine"],
    "north_korea": ["北朝鮮", "North Korea"],
    "sanctions": ["制裁", "sanctions"],
    "tariffs": ["関税", "tariff", "tariffs"],
    "elections": ["選挙", "大統領選", "election", "presidential election"],
    "monetary_policy": ["金融政策", "FOMC", "FRB", "日銀", "利上げ", "利下げ", "central bank", "rate hike", "rate cut", "interest rate decision"],
    "fiscal_policy": ["財政政策", "予算案", "歳出", "fiscal policy", "budget deficit", "government spending"],
    "rates": ["金利", "国債利回り", "interest rate", "bond yield", "Treasury yield"],
    "fx": ["為替", "ドル円", "円安", "円高", "exchange rate", "currency", "yen", "dollar"],
    "inflation": ["インフレ", "物価", "CPI", "inflation", "consumer prices"],
    "employment": ["雇用統計", "失業率", "jobs report", "unemployment", "payrolls"],
    "gdp": ["GDP", "国内総生産", "economic growth"],
    "consumption": ["消費", "個人消費", "consumer spending", "retail sales"],
    "manufacturing": ["製造業", "PMI", "factory activity", "manufacturing sector"],
    "real_estate": ["不動産", "住宅", "real estate", "housing market"],
    "banking": ["銀行", "金融機関", "banking sector", "lender"],
    "insurance": ["保険", "insurer", "insurance"],
    "oil": ["原油", "OPEC", "WTI", "crude oil", "oil prices"],
    "natural_gas": ["天然ガス", "LNG", "natural gas"],
    "gold": ["金価格", "金相場", "gold prices"],
    "copper": ["銅", "copper"],
    "rare_earth": ["レアアース", "希土類", "rare earth"],
    "chemicals": ["化学", "石油化学", "chemical", "petrochemical"],
    "materials": ["素材", "鉄鋼", "steel", "raw materials"],
    "auto": ["自動車", "EV", "automaker", "automotive", "electric vehicle", "carmaker"],
    "semiconductor": ["半導体", "SOX", "chip", "chipmaker", "semiconductor"],
    "ai": ["AI", "人工知能", "生成AI", "artificial intelligence", "generative AI"],
    "software": ["ソフトウェア", "クラウド", "software", "cloud computing"],
    "telecom": ["通信", "5G", "telecom", "5G network"],
    "electric_power": ["電力", "発電", "electricity", "power grid", "power generation"],
    "infrastructure": ["インフラ", "infrastructure"],
    "shipping": ["海運", "コンテナ船", "shipping", "container ship"],
    "logistics": ["物流", "logistics", "supply chain"],
    "retail": ["小売", "retailer", "retail sector"],
    "food": ["食品", "food prices", "agriculture"],
    "healthcare": ["医療", "healthcare", "hospital"],
    "biotech": ["バイオ", "創薬", "biotech", "drug development"],
    "defense": ["防衛", "国防", "defense spending", "military"],
    "space": ["宇宙", "衛星", "space launch", "satellite"],
    "cyber": ["サイバー", "サイバー攻撃", "cyberattack", "cybersecurity"],
    "mna": ["M&A", "買収", "合併", "acquisition", "merger"],
    "earnings": ["決算", "業績", "earnings report", "quarterly results"],
    "regulation": ["規制", "当局", "regulator", "regulatory"],
}


def _keyword_matches(keyword: str, text: str) -> bool:
    """キーワード一致判定（§17: 全カテゴリ同一ロジック）。

    英字キーワード（ASCII）は大文字小文字を無視し単語境界つきで判定する
    （例: "AI" が "said" の中の "ai" に誤反応しない）。日本語はスペース無しで
    連続する文章のため、従来通り単純な部分一致で判定する（単語境界の概念が
    日本語の文字種には馴染まないため）。
    """
    if keyword.isascii():
        return re.search(r"\b" + re.escape(keyword) + r"\b", text, flags=re.IGNORECASE) is not None
    return keyword in text


def classify_article(article: Article, category_keywords: Dict[str, List[str]] = None) -> Article:
    """title_original + description からカテゴリ・テーマを機械的に判定する。

    すべてのカテゴリを同一ロジック（キーワード一致数）で評価するため、
    AI・半導体だけを優遇するコードパスは存在しない（§17）。
    """
    kw_map = category_keywords or DEFAULT_CATEGORY_KEYWORDS
    text = f"{article.title_original} {article.description}"
    hits: Dict[str, int] = {}
    for category, keywords in kw_map.items():
        count = sum(1 for kw in keywords if kw and _keyword_matches(kw, text))
        if count > 0:
            hits[category] = count

    if not hits:
        article.primary_category = "uncategorized"
        article.classification_status = "classified"
        return article

    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))
    article.primary_category = ranked[0][0]
    article.secondary_categories = [c for c, _ in ranked[1:6]]
    article.themes = [c for c, _ in ranked[:6]]
    article.classification_status = "classified"
    return article
