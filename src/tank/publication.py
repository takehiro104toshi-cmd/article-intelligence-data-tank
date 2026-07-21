"""Publication Layer（§6, §7, §8, §26）。

Published Intelligence Package を生成する。フィールドは allowlist 方式で
組み立てるため（`_public_article_view` が公開してよいフィールドだけを列挙する）、
本モジュールは PrivateArticleStore や Article.description の全文を一切参照しない。
これにより「private/restricted本文が配信物に混入する」という失敗モードを
構造的に防ぐ（allowlistに無いフィールドは物理的に出力できない）。

安全な公開手順（§26）:
  1. package を dict として組み立てる
  2. schema validation（必須キー確認）
  3. サイズ上限チェック・超過時トリム
  4. JSON化してgzip圧縮
  5. checksum(sha256) 計算
  6. manifest更新
  7. 一時ファイルへ書き、os.replace で atomic に差し替え（不完全な公開を防ぐ）
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from .diversity import select_diverse
from .models import Article, EventCluster
from .scoring import (
    compute_retrieval_score,
    independent_source_confirmation_score,
    market_reaction_score,
)

SCHEMA_VERSION = "1.0"

DEFAULT_LIMITS = {
    "max_hot_articles": 100,
    "max_global_drivers": 20,
    "max_market_reactions": 30,
    "max_risk_items": 20,
    "max_theme_summary": 30,
    "max_event_clusters": 30,
    "max_historical_matches": 20,
    "max_source_health": 100,
    "package_max_uncompressed_mb": 5,
    "max_published_source_share": 0.20,
}

_RISK_CATEGORIES = {
    "geopolitics", "diplomacy_war", "us_china", "us_iran_middle_east", "taiwan",
    "ukraine", "north_korea", "sanctions", "cyber",
}


def _public_article_view(article: Article) -> dict:
    """§5「Market Intelligenceへ渡してよいもの」だけを含む allowlist ビュー。
    本文（description全文）・private/restricted系フィールドは一切含めない。
    """
    return {
        "article_id": article.article_id,
        "title": article.title_ja or article.title_original,
        "url": article.canonical_url,
        "source": article.source_name,
        "published_at": article.published_at_jst or article.published_at_utc,
        "public_excerpt": article.public_excerpt,
        "category": article.primary_category,
        "themes": list(article.themes),
        "countries": list(article.countries),
        "regions": list(article.regions),
        "companies": list(article.companies),
        "tickers": list(article.tickers),
        "commodities": list(article.commodities),
        "policies": list(article.policies),
        "geopolitical_entities": list(article.geopolitical_entities),
        "importance_score": article.importance_score,
        "market_impact_score": article.market_impact_score,
        "urgency_score": article.urgency_score,
        "structural_score": article.structural_score,
        "source_trust": article.source_trust,
        "causal_keywords": list(article.causal_keywords),
        "event_cluster_id": article.event_cluster_id,
        "potential_risk_score": article.potential_risk_score,
    }


def _cluster_view(cluster: EventCluster) -> dict:
    return {
        "event_cluster_id": cluster.event_cluster_id,
        "event_title": cluster.event_title,
        "category": cluster.category,
        "countries": list(cluster.countries),
        "actors": list(cluster.actors),
        "first_seen_at": cluster.first_seen_at,
        "last_seen_at": cluster.last_seen_at,
        "article_count": cluster.article_count,
        "independent_source_count": cluster.independent_source_count,
        "importance_score": cluster.importance_score,
        "market_impact_score": cluster.market_impact_score,
        "urgency_score": cluster.urgency_score,
        "affected_assets": list(cluster.affected_assets),
        "escalation_status": cluster.escalation_status,
        "priced_in_status": cluster.priced_in_status,
        "representative_article_ids": list(cluster.representative_articles),
    }


def _score_article(article: Article, has_reaction: bool, reaction_magnitude: float, now: datetime) -> float:
    from .scoring import freshness_score as _fresh
    relevance = min(1.0, (article.importance_score + article.market_impact_score) / 2.0) or 0.5
    return compute_retrieval_score(
        relevance=relevance,
        market_reaction=market_reaction_score(has_reaction, reaction_magnitude),
        freshness=_fresh(article.published_at_utc, now),
        source_trust=article.source_trust,
        urgency=article.urgency_score,
        structural=article.structural_score,
        independent_source_confirmation=independent_source_confirmation_score(1),
    )


def build_hot_articles(articles: List[Article], clusters: dict, reaction_lookup: dict,
                       now: datetime, limit: int, max_source_share: float = 0.25) -> List[dict]:
    for a in articles:
        has_reaction = reaction_lookup.get(a.event_cluster_id, (False, 0.0))[0]
        magnitude = reaction_lookup.get(a.event_cluster_id, (False, 0.0))[1]
        a._retrieval_score = _score_article(a, has_reaction, magnitude, now)  # type: ignore[attr-defined]
    # §9: Package選定段階でのみ、同一ソースの占有率に上限を課す（保存は全件維持）。
    selected = select_diverse(articles, target_count=limit, max_source_share=max_source_share)
    return [_public_article_view(a) for a in selected]


def build_global_drivers(clusters: dict, reaction_lookup: dict, limit: int) -> List[dict]:
    """§18 Market Reaction First: ニュース件数でなく実際の市場反応が伴うclusterを優先。"""
    scored = []
    for cluster in clusters.values():
        has_reaction, magnitude = reaction_lookup.get(cluster.event_cluster_id, (False, 0.0))
        score = market_reaction_score(has_reaction, magnitude) * 0.7 + min(1.0, cluster.importance_score) * 0.3
        scored.append((score, cluster))
    scored.sort(key=lambda x: -x[0])
    return [_cluster_view(c) for _, c in scored[:limit]]


def build_risk_radar(clusters: dict, limit: int) -> List[dict]:
    risk = [c for c in clusters.values() if c.category in _RISK_CATEGORIES]
    risk.sort(key=lambda c: -(c.urgency_score + c.importance_score))
    return [_cluster_view(c) for c in risk[:limit]]


def build_theme_summary(articles: List[Article], limit: int) -> List[dict]:
    counts: dict = {}
    for a in articles:
        for theme in (a.themes or [a.primary_category]):
            # "uncategorized"はテーマではなく「分類できなかった」印のため集計から除外する
            # （配信先でノイズが集計最上位に表示されるのを防ぐ）。
            if not theme or theme == "uncategorized":
                continue
            entry = counts.setdefault(theme, {"theme": theme, "article_count": 0, "avg_importance": 0.0, "_sum": 0.0})
            entry["article_count"] += 1
            entry["_sum"] += a.importance_score
    out = []
    for entry in counts.values():
        entry["avg_importance"] = round(entry["_sum"] / entry["article_count"], 4) if entry["article_count"] else 0.0
        del entry["_sum"]
        out.append(entry)
    out.sort(key=lambda e: -e["article_count"])
    return out[:limit]


def build_market_reactions_view(clusters: dict, reaction_store, limit: int) -> List[dict]:
    out = []
    for cluster in clusters.values():
        record = reaction_store.load_all().get(cluster.event_cluster_id)
        if not record:
            continue
        out.append({
            "event_cluster_id": cluster.event_cluster_id,
            "event_title": cluster.event_title,
            "reactions": record,
        })
    out.sort(key=lambda r: -sum(1 for t in r["reactions"].values() for v in t.values() if v is not None))
    return out[:limit]


def build_source_health(cursors: dict, limit: int) -> List[dict]:
    out = []
    for name, cursor in cursors.items():
        out.append({
            "source_name": name,
            "last_fetch_completed_at": cursor.last_fetch_completed_at,
            "consecutive_failures": cursor.consecutive_failures,
            "last_http_status": cursor.last_http_status,
        })
    out.sort(key=lambda r: -r["consecutive_failures"])
    return out[:limit]


def _reaction_lookup(clusters: dict, reaction_store) -> dict:
    """cluster_id -> (has_reaction, magnitude) の軽量ルックアップを1回だけ作る。"""
    lookup = {}
    for cluster_id in clusters:
        has = reaction_store.has_any_reaction(cluster_id)
        mag = reaction_store.reaction_magnitude(cluster_id) if has else 0.0
        lookup[cluster_id] = (has, mag)
    return lookup


def build_package(
    articles: List[Article],
    clusters: dict,
    cursors: dict,
    reaction_store,
    historical_matches: List[dict],
    tank_status: dict,
    quality: dict,
    now: Optional[datetime] = None,
    limits: Optional[dict] = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    reaction_lookup = _reaction_lookup(clusters, reaction_store)

    package = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "generated_at_jst": now.astimezone(timezone(timedelta(hours=9))).isoformat(),
        "tank_status": tank_status,
        "hot_articles": build_hot_articles(
            list(articles), clusters, reaction_lookup, now, limits["max_hot_articles"],
            max_source_share=limits.get("max_published_source_share", 0.25)),
        "global_drivers": build_global_drivers(clusters, reaction_lookup, limits["max_global_drivers"]),
        "market_reactions": build_market_reactions_view(clusters, reaction_store, limits["max_market_reactions"]),
        "risk_radar": build_risk_radar(clusters, limits["max_risk_items"]),
        "theme_summary": build_theme_summary(list(articles), limits["max_theme_summary"]),
        "event_clusters": [_cluster_view(c) for c in list(clusters.values())[: limits["max_event_clusters"]]],
        "historical_matches": historical_matches[: limits["max_historical_matches"]],
        "source_health": build_source_health(cursors, limits["max_source_health"]),
        "quality": quality,
    }
    _enforce_size_limit(package, limits["package_max_uncompressed_mb"])
    return package


def _package_size_mb(package: dict) -> float:
    return len(json.dumps(package, ensure_ascii=False).encode("utf-8")) / (1024 * 1024)


def _enforce_size_limit(package: dict, max_mb: float) -> None:
    """§8, §25: パッケージが上限を超えたら、スコアの低い hot_articles から間引く。"""
    trim_order = ["hot_articles", "theme_summary", "historical_matches", "event_clusters"]
    idx = 0
    while _package_size_mb(package) > max_mb and idx < len(trim_order):
        key = trim_order[idx]
        if len(package[key]) <= 5:
            idx += 1
            continue
        package[key] = package[key][: max(5, len(package[key]) - 10)]


REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version", "generated_at_utc", "generated_at_jst", "tank_status",
    "hot_articles", "global_drivers", "market_reactions", "risk_radar",
    "theme_summary", "event_clusters", "historical_matches", "source_health", "quality",
}


def validate_package_schema(package: dict) -> bool:
    return REQUIRED_TOP_LEVEL_KEYS.issubset(package.keys())


def publish_package(package: dict, output_dir: str) -> dict:
    """§26 安全な公開手順。gzip圧縮・checksum・manifest・atomic replaceを行う。

    戻り値: manifest dict（生成に失敗した場合は publication_status="failed" を含む）。
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not validate_package_schema(package):
        return {"publication_status": "failed", "reason": "schema_validation_failed"}

    raw_json = json.dumps(package, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw_json)
    checksum = hashlib.sha256(compressed).hexdigest()

    package_path = out_dir / "intelligence_package.json.gz"
    manifest_path = out_dir / "manifest.json"

    # 一時ファイルへ書いてから atomic replace（不完全な公開を防ぐ）
    fd, tmp = tempfile.mkstemp(dir=str(out_dir), prefix=".package-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(compressed)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, package_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    manifest = {
        "schema_version": package["schema_version"],
        "generated_at": package["generated_at_utc"],
        "checksum": checksum,
        "compressed_size": len(compressed),
        "uncompressed_size": len(raw_json),
        "item_counts": {
            k: len(package[k]) for k in
            ("hot_articles", "global_drivers", "market_reactions", "risk_radar",
             "theme_summary", "event_clusters", "historical_matches", "source_health")
        },
        "data_range": {
            "oldest": package.get("tank_status", {}).get("latest_article_at", ""),
            "newest": package.get("generated_at_utc", ""),
        },
        "publication_status": "success",
        "package_file": package_path.name,
    }

    fd, tmp2 = tempfile.mkstemp(dir=str(out_dir), prefix=".manifest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp2, manifest_path)
    finally:
        if os.path.exists(tmp2):
            os.remove(tmp2)

    # §12/§13: last-known-good を保持する。検証済みで正常公開できたPackageのコピーをLKGへ複製。
    # 次回runでの新Package生成が失敗しても、latest は atomic replace のため壊れず、
    # さらに直前の正常版を last_known_good/ から復元できる（restore用の明示的バックアップ）。
    try:
        _write_last_known_good(out_dir, compressed, manifest)
    except OSError:
        pass  # LKG保存失敗は致命的でない（latestは既に正常公開済み）。

    return manifest


def _write_last_known_good(out_dir: Path, compressed: bytes, manifest: dict) -> None:
    """検証済みPackageのコピーを last_known_good/ へ atomic 保存する。"""
    lkg_dir = out_dir / "last_known_good"
    lkg_dir.mkdir(parents=True, exist_ok=True)
    lkg_manifest = {**manifest, "role": "last_known_good"}
    for name, data, mode in (
        ("intelligence_package.json.gz", compressed, "wb"),
        ("manifest.json", json.dumps(lkg_manifest, ensure_ascii=False, indent=2).encode("utf-8"), "wb"),
    ):
        dest = lkg_dir / name
        fd, tmp = tempfile.mkstemp(dir=str(lkg_dir), prefix=".lkg-", suffix=".tmp")
        try:
            with os.fdopen(fd, mode) as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
