"""Source Portfolio 管理（Phase 3 Batch 1 §8, §13, §15）。

- Source定義の検証（ID/URL重複・必須field・tier/region/category妥当性）
- Coverage指標の算出（Tier1比率・日本比率・region/category多様性・集中度）
- 企業開示(disclosure)の種別分類（EDGAR等のfiling向け）

保存記事を削除する処理は一切持たない。監視・検証・分類のみ（§9/§10）。
"""
from __future__ import annotations

import collections
from typing import Dict, List, Optional

REQUIRED_FIELDS = ("id", "name", "url", "enabled", "source_class", "region", "language")
# country は国際機関・グローバル情報源では省略可（下の検証で条件付き必須にする）。
VALID_TIERS = {1, 2, 3, 4}
VALID_SOURCE_CLASSES = {
    "primary_official", "official_corporate", "international_institution", "exchange",
    "regulator", "statistics_agency", "major_news", "specialist_media", "aggregator", "unknown",
}
# Tier1 とみなす source_class（一次情報／公式）。
TIER1_CLASSES = {
    "primary_official", "official_corporate", "international_institution",
    "exchange", "regulator", "statistics_agency",
}


def validate_sources(sources: List[dict]) -> List[str]:
    """Source定義の妥当性を検査し、問題メッセージのリストを返す（空なら問題なし）。"""
    errors: List[str] = []
    seen_ids: Dict[str, int] = collections.Counter(s.get("id", "") for s in sources)
    seen_urls: Dict[str, int] = collections.Counter((s.get("url", "") or "").strip() for s in sources)

    for dup_id, n in seen_ids.items():
        if dup_id and n > 1:
            errors.append(f"duplicate source id: {dup_id} ({n}件)")
    for dup_url, n in seen_urls.items():
        if dup_url and n > 1:
            errors.append(f"duplicate source url: {dup_url} ({n}件)")

    for s in sources:
        sid = s.get("id", "<no-id>")
        for f in REQUIRED_FIELDS:
            if f not in s or s.get(f) in (None, ""):
                # enabled は False も有効値なので存在チェックのみ
                if f == "enabled" and "enabled" in s:
                    continue
                errors.append(f"[{sid}] missing required field: {f}")
        # country は国際機関/aggregator/グローバル情報源では省略可（単一国に帰属しないため）。
        if not s.get("country"):
            if (s.get("source_class") not in ("international_institution", "aggregator")
                    and (s.get("region") or "") != "Global"):
                errors.append(f"[{sid}] missing required field: country")
        sc = s.get("source_class")
        if sc and sc not in VALID_SOURCE_CLASSES:
            errors.append(f"[{sid}] invalid source_class: {sc}")
        tier = s.get("tier")
        if tier is not None and tier not in VALID_TIERS:
            errors.append(f"[{sid}] invalid tier: {tier}")
        ts = s.get("trust_score")
        if ts is not None and not (0 <= ts <= 100):
            errors.append(f"[{sid}] trust_score out of range 0-100: {ts}")
        mi = s.get("max_items_per_fetch")
        if mi is not None and (not isinstance(mi, int) or mi <= 0):
            errors.append(f"[{sid}] invalid max_items_per_fetch: {mi}")
    return errors


def _tier_of(s: dict) -> int:
    """明示tierがあればそれを、無ければ source_class から推定する。"""
    if s.get("tier") in VALID_TIERS:
        return int(s["tier"])
    sc = s.get("source_class", "")
    if sc in TIER1_CLASSES:
        return 1
    if sc == "major_news":
        return 2
    if sc == "specialist_media":
        return 3
    if sc == "aggregator":
        return 4
    return 4


def _share(counter: Dict[str, int], total: int) -> Dict[str, float]:
    return {k: round(v / total, 4) for k, v in counter.items()} if total else {}


def coverage_metrics(sources: List[dict], enabled_only: bool = True) -> dict:
    """enabled（既定）ソース群のCoverage指標を返す（§13, §15）。"""
    pool = [s for s in sources if s.get("enabled")] if enabled_only else list(sources)
    total = len(pool)
    if total == 0:
        return {"enabled_total": 0}

    tier_counts = collections.Counter(_tier_of(s) for s in pool)
    class_counts = collections.Counter(s.get("source_class", "unknown") for s in pool)
    region_counts = collections.Counter(s.get("region", "?") or "?" for s in pool)
    country_counts = collections.Counter(s.get("country", "?") or "?" for s in pool)
    lang_counts = collections.Counter(s.get("language", "?") or "?" for s in pool)
    cat_counts = collections.Counter(s.get("primary_category", "?") or "?" for s in pool)

    tier1 = tier_counts.get(1, 0)
    japan = country_counts.get("JP", 0)
    us = country_counts.get("US", 0)
    top_region, top_region_n = (region_counts.most_common(1)[0] if region_counts else ("", 0))
    top_cat, top_cat_n = (cat_counts.most_common(1)[0] if cat_counts else ("", 0))

    return {
        "enabled_total": total,
        "tier1_share": round(tier1 / total, 4),
        "japan_share": round(japan / total, 4),
        "us_share": round(us / total, 4),
        "primary_source_share": round(sum(class_counts.get(c, 0) for c in TIER1_CLASSES) / total, 4),
        "tier_distribution": dict(sorted(tier_counts.items())),
        "source_class_distribution": dict(class_counts),
        "region_distribution": _share(dict(region_counts), total),
        "country_distribution": _share(dict(country_counts), total),
        "language_distribution": _share(dict(lang_counts), total),
        "category_distribution": _share(dict(cat_counts), total),
        "region_diversity": len(region_counts),
        "category_diversity": len(cat_counts),
        "language_diversity": len(lang_counts),
        "top_region": top_region,
        "top_region_share": round(top_region_n / total, 4),
        "top_category": top_cat,
        "top_category_share": round(top_cat_n / total, 4),
    }


def coverage_gaps(metrics: dict, region_balance: Optional[dict] = None) -> List[str]:
    """Coverage指標から不足・偏重の警告メッセージを返す（強制削除はしない・監視のみ）。"""
    rb = region_balance or {}
    warns: List[str] = []
    if metrics.get("enabled_total", 0) == 0:
        return ["enabled source が 0 件です。"]
    if metrics["japan_share"] < rb.get("minimum_japan_share", 0.12):
        warns.append(f"日本比率が低い: {metrics['japan_share']:.0%} < {rb.get('minimum_japan_share',0.12):.0%}")
    if metrics["us_share"] < rb.get("minimum_us_share", 0.18):
        warns.append(f"米国比率が低い: {metrics['us_share']:.0%}")
    if metrics["top_region_share"] > rb.get("max_single_region_share", 0.35):
        warns.append(f"地域集中: {metrics['top_region']} {metrics['top_region_share']:.0%} > 上限")
    if metrics["tier1_share"] < 0.35:
        warns.append(f"一次情報比率が目標未満: {metrics['tier1_share']:.0%} < 35%")
    return warns


# ---------- 企業開示の種別分類（§11） ----------

_DISCLOSURE_RULES = (
    ("guidance_revision", ("業績予想", "guidance", "revised outlook", "revises", "forecast revision")),
    ("earnings", ("決算", "earnings", "quarterly results", "10-q", "10-k", "6-k", "financial results")),
    ("buyback", ("自己株式", "自社株買い", "buyback", "share repurchase", "repurchase")),
    ("dividend", ("配当", "dividend")),
    ("M&A", ("買収", "合併", "acquisition", "merger", "takeover", "tender offer")),
    ("major_contract", ("大型受注", "受注", "major contract", "awarded", "order")),
    ("capex", ("設備投資", "capital expenditure", "capex", "plant investment")),
    ("executive_change", ("役員", "代表取締役", "代表者", "異動", "ceo", "cfo", "executive change", "appoint")),
    ("litigation", ("訴訟", "提訴", "lawsuit", "litigation", "settlement")),
    ("regulatory_action", ("行政処分", "regulatory", "sanction", "penalty", "enforcement")),
    ("financing", ("増資", "社債", "financing", "offering", "notes due", "loan")),
    ("restructuring", ("リストラ", "restructuring", "reorganization", "spin-off", "divestiture")),
    ("bankruptcy", ("破綻", "民事再生", "会社更生", "bankruptcy", "chapter 11", "insolvency")),
)

# 定型・低重要度とみなす開示（Candidateを埋めないよう重要度を下げる目安）。
_LOW_MATERIALITY = {"other"}


def classify_disclosure(title: str, filing_type: str = "") -> dict:
    """開示タイトル/フォーム種別から disclosure_type と materiality の目安を返す（§11）。

    LLMを使わず語彙マッチのみ。判定できないものは other（低materiality）。
    """
    text = f"{title or ''} {filing_type or ''}".lower()
    for dtype, keys in _DISCLOSURE_RULES:
        if any(k.lower() in text for k in keys):
            # 高materiality: 予想修正/M&A/buyback/大型受注/訴訟/規制/破綻/経営者変更
            high = dtype in {"guidance_revision", "M&A", "buyback", "major_contract",
                             "litigation", "regulatory_action", "bankruptcy", "executive_change",
                             "capex", "dividend"}
            return {"disclosure_type": dtype, "materiality": "high" if high else "medium"}
    return {"disclosure_type": "other", "materiality": "low"}
