"""URL 正規化・トラッキングパラメータ除去（§9, §30-3/4）。

article_id の安定性は canonical_url のハッシュに依存するため、同じ記事が
別クエリパラメータ付きで再取得されても同一 article_id になるよう正規化する。
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "yclid", "igshid",
    "mc_cid", "mc_eid", "ref", "ref_src", "ref_url", "spm", "cmpid",
    "ito", "src", "from", "amp",
}


def _is_tracking_param(key: str) -> bool:
    lk = key.lower()
    return lk in _TRACKING_PARAMS or any(lk.startswith(p) for p in _TRACKING_PARAM_PREFIXES)


def normalize_url(raw_url: str) -> str:
    """スキーム/ホストを小文字化、フラグメント除去、トラッキングパラメータ除去、
    残ったクエリをキー順にソートし、末尾スラッシュを除去した正規化URLを返す。
    """
    if not raw_url:
        return ""
    parts = urlsplit(raw_url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or ""
    query_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking_param(k)]
    query_pairs.sort(key=lambda kv: kv[0])
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))  # fragment除去


def source_domain_of(raw_url: str) -> str:
    parts = urlsplit(raw_url.strip()) if raw_url else None
    if not parts or not parts.netloc:
        return ""
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host
