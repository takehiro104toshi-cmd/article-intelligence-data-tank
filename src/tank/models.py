"""Article / EventCluster / SourceCursor スキーマ（§9, §10, §22）。

外部依存なし（標準ライブラリのみ）。dataclass は asdict() でそのまま JSON 化できる
ようフィールドをプリミティブ型（str/int/float/bool/list/dict/None）だけで構成する。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class Article:
    # 識別
    article_id: str = ""
    canonical_url: str = ""
    normalized_url: str = ""
    source_name: str = ""
    source_domain: str = ""
    source_type: str = ""          # rss / api / official / gov / central_bank / ir / user_private
    source_trust: float = 0.5      # 0.0-1.0
    source_country: str = ""
    language: str = "ja"

    # 本文・権利
    title_original: str = ""
    title_ja: str = ""
    description: str = ""
    public_excerpt: str = ""
    body_storage_type: str = "none"   # "none" / "public_excerpt" / "private"
    body_available: bool = False
    rights_classification: str = "public"  # "public" / "restricted" / "private"

    # 日時（ISO8601文字列。UTC/JSTの両方を保持）
    published_at_utc: str = ""
    published_at_jst: str = ""
    fetched_at_utc: str = ""
    fetched_at_jst: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""
    # 日付品質（Production Stabilization §7）:
    #   published_at が未来/超過去/解析不能で fetched_at へ補正された場合 True。
    #   raw_published_at には元フィードの公開日時文字列をそのまま保持し、後から検証可能にする。
    #   ※記事は破棄せず補正のみ行う（データ損失を避ける）。
    date_inferred: bool = False
    raw_published_at: str = ""

    # 分類
    primary_category: str = ""
    secondary_categories: List[str] = field(default_factory=list)
    themes: List[str] = field(default_factory=list)
    industries: List[str] = field(default_factory=list)
    sectors: List[str] = field(default_factory=list)
    companies: List[str] = field(default_factory=list)
    tickers: List[str] = field(default_factory=list)
    countries: List[str] = field(default_factory=list)
    regions: List[str] = field(default_factory=list)
    commodities: List[str] = field(default_factory=list)
    currencies: List[str] = field(default_factory=list)
    central_banks: List[str] = field(default_factory=list)
    policies: List[str] = field(default_factory=list)
    geopolitical_entities: List[str] = field(default_factory=list)
    event_type: str = ""

    # 分析
    importance_score: float = 0.0
    market_impact_score: float = 0.0
    urgency_score: float = 0.0
    structural_score: float = 0.0
    freshness_score: float = 0.0
    source_score: float = 0.0
    sentiment: str = "neutral"
    expected_direction: str = "unknown"
    affected_assets: List[str] = field(default_factory=list)
    causal_keywords: List[str] = field(default_factory=list)
    search_keywords: List[str] = field(default_factory=list)
    potential_risk_score: float = 0.0
    priced_in_status: str = "unknown"

    # 統合
    content_hash: str = ""
    title_hash: str = ""
    canonical_hash: str = ""
    duplicate_group_id: str = ""
    event_cluster_id: str = ""

    # 処理
    ingestion_run_id: str = ""
    parser_version: str = "1.0"
    classifier_version: str = "1.0"
    schema_version: str = "1.0"
    classification_status: str = "pending"   # pending / classified / error
    error_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Article":
        known = {f for f in Article.__dataclass_fields__}
        return Article(**{k: v for k, v in d.items() if k in known})


@dataclass
class EventCluster:
    event_cluster_id: str = ""
    event_title: str = ""
    category: str = ""
    countries: List[str] = field(default_factory=list)
    actors: List[str] = field(default_factory=list)
    first_seen_at: str = ""
    last_seen_at: str = ""
    article_count: int = 0
    independent_source_count: int = 0
    source_trust_max: float = 0.0
    importance_score: float = 0.0
    market_impact_score: float = 0.0
    urgency_score: float = 0.0
    affected_assets: List[str] = field(default_factory=list)
    market_reaction: dict = field(default_factory=dict)
    escalation_status: str = "steady"   # steady / escalating / de-escalating
    priced_in_status: str = "unknown"
    representative_articles: List[str] = field(default_factory=list)  # article_id のみ（本文は含めない）
    # 内部用: クラスタに属する全記事idの追跡（Published Packageへは出さない。§6の境界を守るため
    # publication.py の _cluster_view は representative_articles のみを公開する）。
    member_article_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "EventCluster":
        known = {f for f in EventCluster.__dataclass_fields__}
        return EventCluster(**{k: v for k, v in d.items() if k in known})


@dataclass
class SourceCursor:
    source_name: str = ""
    last_fetch_started_at: str = ""
    last_fetch_completed_at: str = ""
    latest_published_at: str = ""
    latest_article_id: str = ""
    etag: str = ""
    last_modified: str = ""
    last_http_status: int = 0
    consecutive_failures: int = 0
    next_retry_at: str = ""
    # Production News Sources Phase（§8）で追加。既存記録との後方互換のためデフォルト付き。
    last_success_count: int = 0
    last_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SourceCursor":
        known = {f for f in SourceCursor.__dataclass_fields__}
        return SourceCursor(**{k: v for k, v in d.items() if k in known})


# Market Reaction Schema（§21）: 資産×時間窓。バックフィルは必須ではなく、
# 新着記事から記録を開始できる形にする（値は None のまま保持してよい）。
MARKET_REACTION_WINDOWS = ["1h", "4h", "1d", "5d", "20d"]
MARKET_REACTION_TARGETS = [
    "nikkei225", "topix", "sp500", "nasdaq", "sox", "vix",
    "usdjpy", "dxy", "us10y", "wti", "gold", "bitcoin",
    "related_sectors", "related_stocks",
]


def new_market_reaction_stub() -> Dict[str, Dict[str, Optional[float]]]:
    """新着イベント用の市場反応レコードの空枠を作る（§21）。全件バックフィルは不要。"""
    return {target: {w: None for w in MARKET_REACTION_WINDOWS} for target in MARKET_REACTION_TARGETS}
