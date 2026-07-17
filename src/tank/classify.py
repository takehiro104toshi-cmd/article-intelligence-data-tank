"""キーワードベースの機械的分類（§9 分類フィールド, §17 公平性）。

生成AIは使わない。config.yaml の `categories` に列挙されたテーマを、同一の
アルゴリズムで並列に評価する（AI・半導体だけを特別扱いするコードパスは存在しない）。
一致件数が最大のカテゴリを primary_category、閾値以上一致した他カテゴリを
secondary_categories／themes とする。
"""
from __future__ import annotations

from typing import Dict, List

from .models import Article

# §17 で列挙された分野を「公平に」扱うための既定キーワード（categories）。
# config.yaml で上書き・追加可能（利用者が実際のニュースソースに合わせて調整する）。
DEFAULT_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "geopolitics": ["地政学", "紛争", "軍事", "衝突"],
    "diplomacy_war": ["戦争", "外交", "停戦", "侵攻"],
    "us_china": ["米中", "中国", "台湾海峡"],
    "us_iran_middle_east": ["イラン", "中東", "ホルムズ海峡", "イスラエル"],
    "taiwan": ["台湾"],
    "ukraine": ["ウクライナ"],
    "north_korea": ["北朝鮮"],
    "sanctions": ["制裁"],
    "tariffs": ["関税"],
    "elections": ["選挙", "大統領選"],
    "monetary_policy": ["金融政策", "FOMC", "FRB", "日銀", "利上げ", "利下げ"],
    "fiscal_policy": ["財政政策", "予算案", "歳出"],
    "rates": ["金利", "国債利回り"],
    "fx": ["為替", "ドル円", "円安", "円高"],
    "inflation": ["インフレ", "物価", "CPI"],
    "employment": ["雇用統計", "失業率"],
    "gdp": ["GDP", "国内総生産"],
    "consumption": ["消費", "個人消費"],
    "manufacturing": ["製造業", "PMI"],
    "real_estate": ["不動産", "住宅"],
    "banking": ["銀行", "金融機関"],
    "insurance": ["保険"],
    "oil": ["原油", "OPEC", "WTI"],
    "natural_gas": ["天然ガス", "LNG"],
    "gold": ["金価格", "金相場"],
    "copper": ["銅"],
    "rare_earth": ["レアアース", "希土類"],
    "chemicals": ["化学", "石油化学"],
    "materials": ["素材", "鉄鋼"],
    "auto": ["自動車", "EV"],
    "semiconductor": ["半導体", "SOX"],
    "ai": ["AI", "人工知能", "生成AI"],
    "software": ["ソフトウェア", "クラウド"],
    "telecom": ["通信", "5G"],
    "electric_power": ["電力", "発電"],
    "infrastructure": ["インフラ"],
    "shipping": ["海運", "コンテナ船"],
    "logistics": ["物流"],
    "retail": ["小売"],
    "food": ["食品"],
    "healthcare": ["医療"],
    "biotech": ["バイオ", "創薬"],
    "defense": ["防衛", "国防"],
    "space": ["宇宙", "衛星"],
    "cyber": ["サイバー", "サイバー攻撃"],
    "mna": ["M&A", "買収", "合併"],
    "earnings": ["決算", "業績"],
    "regulation": ["規制", "当局"],
}


def classify_article(article: Article, category_keywords: Dict[str, List[str]] = None) -> Article:
    """title_original + description からカテゴリ・テーマを機械的に判定する。

    すべてのカテゴリを同一ロジック（キーワード一致数）で評価するため、
    AI・半導体だけを優遇するコードパスは存在しない（§17）。
    """
    kw_map = category_keywords or DEFAULT_CATEGORY_KEYWORDS
    text = f"{article.title_original} {article.description}"
    hits: Dict[str, int] = {}
    for category, keywords in kw_map.items():
        count = sum(1 for kw in keywords if kw and kw in text)
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
