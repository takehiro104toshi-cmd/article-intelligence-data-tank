"""ニュースソース設定の読み込みと正規化（§5）。

ソースをコードへハードコードせず、config/sources.yaml から管理する。
config.yaml 内の `sources:`（後方互換）も引き続き受け付ける。
各ソースへデフォルトを補完し、enabled のものだけを抽出できるようにする。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

VALID_FORMATS = {"auto", "rss", "atom"}
VALID_SOURCE_CLASSES = {
    "primary_official", "official_corporate", "international_institution",
    "major_news", "specialist_media", "aggregator", "unknown",
}


def _normalize_source(raw: dict) -> dict:
    """1ソース定義へデフォルトを補完し、内部で使うキーへ揃える（後方互換込み）。"""
    src = dict(raw)
    # 後方互換: 旧 config.yaml の sources は {name,url,type,trust,country,language}
    src.setdefault("id", src.get("name", "").strip().lower().replace(" ", "_") or "unknown")
    src.setdefault("name", src.get("id", "unknown"))
    src.setdefault("enabled", True)
    fmt = src.get("format", src.get("type", "auto"))
    src["format"] = fmt if fmt in VALID_FORMATS else "auto"
    src.setdefault("source_class", "unknown")
    if src["source_class"] not in VALID_SOURCE_CLASSES:
        src["source_class"] = "unknown"
    src.setdefault("country", "")
    src.setdefault("region", "")
    src.setdefault("language", "en")
    src.setdefault("primary_category", "")
    # trust_score(0-100) と ingestion 側が使う trust(0-1) の両方を用意する。
    trust_score = src.get("trust_score")
    if trust_score is None:
        # 旧 trust(0-1) があれば 0-100 へ、無ければ既定 50
        trust_score = int(round(float(src.get("trust", 0.5)) * 100)) if "trust" in src else 50
    src["trust_score"] = int(trust_score)
    src["trust"] = max(0.0, min(1.0, src["trust_score"] / 100.0))
    src.setdefault("fetch_interval_minutes", 60)
    return src


def load_sources(
    config: dict,
    base_dir: Optional[Path] = None,
    sources_file_key: str = "sources_file",
) -> List[dict]:
    """config から全ソース定義（正規化済み）を返す。

    優先順位:
      1. config[sources_file_key] が指すYAMLの `sources:`（推奨・config/sources.yaml）
      2. config['sources']（後方互換・config.yaml 直書き）
    両方あれば結合する（id で重複排除、後勝ち）。
    """
    base_dir = Path(base_dir) if base_dir else Path(".")
    merged: dict = {}

    inline = config.get("sources") or []
    for raw in inline:
        s = _normalize_source(raw)
        merged[s["id"]] = s

    sources_path = config.get(sources_file_key)
    if sources_path:
        path = base_dir / sources_path
        if path.exists():
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for raw in (doc.get("sources") or []):
                s = _normalize_source(raw)
                merged[s["id"]] = s

    return list(merged.values())


def enabled_sources(sources: List[dict]) -> List[dict]:
    return [s for s in sources if s.get("enabled", True) and s.get("url")]
