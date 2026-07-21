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
    """正規化URLを返す（Phase 3 §12 dedup検証で強化）。

    - スキームは https へ畳む（http/https差異で同一記事を別物にしない）
    - ホスト名を小文字化し、先頭 "www." を除去（www有無差異を吸収）
    - フラグメント除去
    - トラッキングパラメータ除去（utm_* / fbclid / gclid 等）
    - 残ったクエリをキー順にソート
    - 末尾スラッシュ除去

    ※これらはすべて「同一リソースを指す表記ゆれ」の吸収であり、異なる記事を
    　統合しない。canonical_url のハッシュが article_id の安定性を担保する。
    """
    if not raw_url:
        return ""
    parts = urlsplit(raw_url.strip())
    # http/https を https に畳む（同一記事の scheme 差異による重複すり抜けを防ぐ）。
    scheme = (parts.scheme or "https").lower()
    if scheme in ("http", "https"):
        scheme = "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]  # www有無の差異を吸収
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
