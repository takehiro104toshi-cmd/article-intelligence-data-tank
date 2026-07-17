"""Event Clustering（§22, §30-13/14/15/16）。

同じ出来事を報じる複数記事を1つの EventCluster へ統合する。判定は
「カテゴリ一致 + エンティティ（国/企業/地政学アクター）の重なり + 時間近接 +
タイトルの語彙重複（Jaccard）」の組み合わせで行う、決定的（非機械学習）なルール。
生成AIは使わない。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .models import Article, EventCluster

_STOPWORDS = {"の", "を", "に", "は", "が", "と", "で", "た", "し", "て", "も", "な", "する", "した"}
_ASCII_RUN = re.compile(r"[A-Za-z0-9]+")
_CJK_RUN = re.compile(r"[一-龯ぁ-んァ-ヶ]+")


def _tokenize(title: str) -> set:
    """日本語は分かち書き辞書が無いため、連続するCJK区間を文字2-gram（シェイングル）に
    分解して疑似トークンとする。英数字区間はそのまま単語として扱う。
    これにより「米政府がイランへ警告」と「イラン政府が米国へ反発」のような、
    語順・活用が違う見出し同士でも共通部分文字列（例:「イラ」「政府」）で重なりを検出できる。
    """
    tokens: set = set()
    for run in _ASCII_RUN.findall(title or ""):
        low = run.lower()
        if low not in _STOPWORDS and len(low) > 1:
            tokens.add(low)
    for run in _CJK_RUN.findall(title or ""):
        if len(run) <= 1:
            continue
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if bigram not in _STOPWORDS:
                tokens.add(bigram)
    return tokens


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _entities(article: Article) -> set:
    return set(article.countries) | set(article.companies) | set(article.geopolitical_entities)


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_matching_cluster(
    article: Article,
    clusters: Dict[str, EventCluster],
    articles_by_id: Dict[str, Article],
    window_hours: int = 72,
    jaccard_threshold: float = 0.2,
) -> Optional[str]:
    """記事が既存のどの EventCluster に属するか判定する。一致がなければ None（＝新規cluster）。

    §30-14「異なる記事を誤統合しない」を満たすため、カテゴリ不一致・エンティティ無共通・
    時間窓外・語彙重複が閾値未満のいずれかで即座に不一致と判定する（曖昧な統合をしない）。
    """
    art_dt = _parse_dt(article.published_at_utc)
    art_tokens = _tokenize(article.title_original)
    art_entities = _entities(article)

    best_id = None
    best_score = 0.0
    for cluster_id, cluster in clusters.items():
        if cluster.category != article.primary_category:
            continue
        if not (set(cluster.countries) & art_entities or set(cluster.actors) & art_entities):
            continue
        last_dt = _parse_dt(cluster.last_seen_at)
        if art_dt and last_dt and abs((art_dt - last_dt).total_seconds()) > window_hours * 3600:
            continue
        # 代表記事のタイトルと語彙重複を見る
        rep_tokens = set()
        for rep_id in cluster.representative_articles:
            rep = articles_by_id.get(rep_id)
            if rep:
                rep_tokens |= _tokenize(rep.title_original)
        score = _jaccard(art_tokens, rep_tokens)
        if score >= jaccard_threshold and score > best_score:
            best_score = score
            best_id = cluster_id
    return best_id


def new_cluster_id(article: Article) -> str:
    base = re.sub(r"[^a-z0-9]", "", (article.article_id or "")[-12:])
    return f"evc_{base}"


def upsert_cluster(
    clusters: Dict[str, EventCluster],
    cluster_id: str,
    article: Article,
    articles_by_id: Dict[str, Article],
    max_representatives: int = 3,
) -> EventCluster:
    """記事をクラスタへ統合し、統計を再計算する（§22, §30-15/16）。

    articles_by_id は呼び出し側の記事辞書。この関数自身が article を登録するため、
    呼び出し側で upsert_cluster より前に登録し忘れても代表記事選定が壊れない。
    """
    articles_by_id[article.article_id] = article
    cluster = clusters.get(cluster_id)
    if cluster is None:
        cluster = EventCluster(
            event_cluster_id=cluster_id,
            event_title=article.title_original,
            category=article.primary_category,
            countries=list(article.countries),
            actors=list(article.geopolitical_entities),
            first_seen_at=article.published_at_utc,
        )
        clusters[cluster_id] = cluster

    cluster.countries = sorted(set(cluster.countries) | set(article.countries))
    cluster.actors = sorted(set(cluster.actors) | set(article.geopolitical_entities))
    if not cluster.last_seen_at or (article.published_at_utc and article.published_at_utc > cluster.last_seen_at):
        cluster.last_seen_at = article.published_at_utc
    if not cluster.first_seen_at or (article.published_at_utc and article.published_at_utc < cluster.first_seen_at):
        cluster.first_seen_at = article.published_at_utc

    # article_count/independent_source_count 等は「これまで統合された全記事」から計算する
    # （representative_articles は表示用に上位3件だけへ絞るため、それとは別に全件を追跡する）。
    member_ids = set(cluster.member_article_ids)
    member_ids.add(article.article_id)
    cluster.member_article_ids = sorted(member_ids)
    member_articles = [articles_by_id[i] for i in member_ids if i in articles_by_id]
    cluster.article_count = len(member_ids)
    cluster.independent_source_count = len({a.source_domain for a in member_articles if a.source_domain})
    cluster.source_trust_max = max([a.source_trust for a in member_articles] + [cluster.source_trust_max])
    cluster.importance_score = max([a.importance_score for a in member_articles] + [cluster.importance_score])
    cluster.market_impact_score = max([a.market_impact_score for a in member_articles] + [cluster.market_impact_score])
    cluster.urgency_score = max([a.urgency_score for a in member_articles] + [cluster.urgency_score])
    cluster.affected_assets = sorted({asset for a in member_articles for asset in a.affected_assets})

    # escalation_status: 直近の記事数増加ペースから単純判定（生成AIを使わない機械的判定）
    if cluster.article_count >= 5:
        cluster.escalation_status = "escalating"
    elif cluster.article_count <= 1:
        cluster.escalation_status = "steady"

    # representative_articles: importance + source_trust 上位を最大 max_representatives 件（§30-15）
    ranked = sorted(member_articles, key=lambda a: (-(a.importance_score + a.source_trust), a.published_at_utc or ""))
    cluster.representative_articles = [a.article_id for a in ranked[:max_representatives]]

    article.event_cluster_id = cluster_id
    return cluster
