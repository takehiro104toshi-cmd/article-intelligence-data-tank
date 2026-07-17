"""重複排除（§9 統合フィールド, §30 exact/syndicated duplicate）。

3種のハッシュを使う:
  canonical_hash … 正規化URLのハッシュ。同一URLの完全重複を検出。
  content_hash   … タイトル+本文抜粋の正規化テキストのハッシュ。表記ゆれに強い完全重複検出。
  title_hash     … タイトルのみの正規化ハッシュ。配信元違いの「再配信記事」検出に使う
                   （syndicated duplicate = 同一タイトルだが source_domain が異なる）。
"""
from __future__ import annotations

import hashlib
import re
from typing import List, Optional

from .models import Article


def _normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[　 ]", " ", text)
    text = re.sub(r"[^\w\s]", "", text)  # 記号除去（表記ゆれ吸収）
    return text.strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_hashes(article: Article) -> Article:
    article.canonical_hash = _sha256(article.canonical_url)
    article.title_hash = _sha256(_normalize_text(article.title_original))
    body_for_hash = _normalize_text(article.title_original) + "|" + _normalize_text(article.description)
    article.content_hash = _sha256(body_for_hash)
    return article


def article_id_from_canonical(canonical_url: str) -> str:
    """article_id は canonical_url のハッシュから安定的に決める（§30-2）。"""
    return "art_" + _sha256(canonical_url)[:24]


def is_exact_duplicate(a: Article, b: Article) -> bool:
    return bool(a.canonical_hash) and (a.canonical_hash == b.canonical_hash or a.content_hash == b.content_hash)


def is_syndicated_duplicate(a: Article, b: Article) -> bool:
    """同一タイトル（title_hash一致）だが配信元(source_domain)が異なる＝再配信記事。"""
    return (
        bool(a.title_hash)
        and a.title_hash == b.title_hash
        and a.source_domain != b.source_domain
        and not is_exact_duplicate(a, b)
    )


def pick_representative(group: List[Article]) -> Article:
    """再配信記事グループの代表1件を選ぶ（source_trust優先、次に早い公開時刻）。"""
    return sorted(group, key=lambda a: (-a.source_trust, a.published_at_utc or "9999"))[0]


def find_duplicate_group(new_article: Article, existing_by_canonical: dict, existing_by_title: dict) -> Optional[str]:
    """既存インデックスと突き合わせ、重複ならグループIDを返す（無ければ None＝新規記事）。

    existing_by_canonical: canonical_hash -> duplicate_group_id
    existing_by_title:     title_hash -> duplicate_group_id
    """
    if new_article.canonical_hash in existing_by_canonical:
        return existing_by_canonical[new_article.canonical_hash]
    if new_article.content_hash in existing_by_canonical:
        return existing_by_canonical[new_article.content_hash]
    if new_article.title_hash in existing_by_title:
        return existing_by_title[new_article.title_hash]
    return None
