"""RSS 2.0 / Atom フィードのパーサ（Production News Sources Phase §3）。

標準ライブラリ xml.etree.ElementTree のみで実装（外部依存を増やさない）。
feedparser は堅牢だが依存が増えるため、本プロジェクトでは stdlib を採用し、
オフラインで決定的にテストできるようにする（要件 §4 の「信頼できる既存ライブラリ優先」を
標準ライブラリ xml.etree で満たす）。

対応: RSS 2.0 / Atom / XML namespace / HTML entity / CDATA / relative URL /
published・updated 日時 / 日時欠損 / malformed item（その item だけスキップ） /
malformed feed（空リストを返す・例外を投げない）。

出力: 正規化前の生レコード（dict）のリスト。build_article_from_raw が期待する
キー（title / url / description / published_at_utc）に加え、updated_at_utc / source を持つ。
本文全文の取得は行わず、RSS summary / Atom summary からの短い抜粋のみ扱う（§7）。
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

_ATOM_NS = "http://www.w3.org/2005/Atom"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MAX_EXCERPT = 400


def _localname(tag: str) -> str:
    """'{namespace}local' → 'local'。namespace を無視してタグ名だけを取り出す。"""
    if tag and tag[0] == "{":
        return tag.rsplit("}", 1)[1]
    return tag or ""


def strip_html(text: Optional[str]) -> str:
    """HTMLタグを除去し、HTMLエンティティを復号して安全なプレーンテキストにする（§2.7）。"""
    if not text:
        return ""
    # まずHTMLエンティティを復号（&amp; → & 等）、その後タグ除去、最後に再度復号
    # （&lt;b&gt; のようにエンコードされたタグにも対応）。
    decoded = html.unescape(text)
    no_tags = _TAG_RE.sub(" ", decoded)
    no_tags = html.unescape(no_tags)
    return _WS_RE.sub(" ", no_tags).strip()


def _parse_datetime(value: Optional[str]) -> str:
    """RSS(RFC822) / Atom(RFC3339) の日時を UTC ISO8601 文字列へ正規化する。
    解析できない・欠損は空文字を返す（推測しない）。"""
    if not value:
        return ""
    value = value.strip()
    # RFC3339 / ISO8601（Atom の published/updated）
    try:
        iso = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    # RFC822（RSS の pubDate）
    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def _decode(content: bytes) -> Optional[str]:
    """バイト列を文字列へ。XML宣言のencodingを尊重しつつ、失敗時はUTF-8/置換でフォールバック。"""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    # BOM 除去
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if content.startswith(bom):
            try:
                return content[len(bom):].decode(enc)
            except UnicodeDecodeError:
                break
    # XML宣言から encoding を検出
    m = re.match(rb"<\?xml[^>]*encoding=[\"']([\w\-]+)[\"']", content[:200])
    if m:
        try:
            return content.decode(m.group(1).decode("ascii", "ignore"))
        except (LookupError, UnicodeDecodeError):
            pass
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _first_text(elem, names) -> str:
    for child in list(elem):
        if _localname(child.tag) in names:
            if child.text and child.text.strip():
                return child.text
    return ""


def _rss_item(item, source_url: str) -> Optional[dict]:
    try:
        title = strip_html(_first_text(item, {"title"}))
        link = _first_text(item, {"link"}).strip()
        # guid が permalink の場合のフォールバック
        if not link:
            for child in list(item):
                if _localname(child.tag) == "guid" and (child.text or "").startswith("http"):
                    link = child.text.strip()
                    break
        link = urljoin(source_url, link) if link else ""
        desc = strip_html(_first_text(item, {"description", "summary", "encoded"}))
        pub_raw = _first_text(item, {"pubDate", "date", "published"}).strip()
        pub = _parse_datetime(pub_raw)
        upd = _parse_datetime(_first_text(item, {"updated", "modified"}))
        if not title and not link:
            return None  # タイトルもURLも無い item は捨てる（malformed item）
        return {
            "title": title,
            "url": link,
            "description": desc[:_MAX_EXCERPT],
            "published_at_utc": pub or upd,
            "updated_at_utc": upd,
            # 元の公開日時文字列（日付品質ガード用。異常検出時の検証に使う）。
            "published_raw": pub_raw,
        }
    except Exception:  # noqa: BLE001  1件の破損で全体を止めない
        return None


def _atom_entry(entry, source_url: str) -> Optional[dict]:
    try:
        title = strip_html(_first_text(entry, {"title"}))
        link = ""
        # rel="alternate" を優先、無ければ最初の link href
        for child in list(entry):
            if _localname(child.tag) == "link":
                rel = child.get("rel", "alternate")
                href = child.get("href", "")
                if href and (rel == "alternate" or not link):
                    link = href
                    if rel == "alternate":
                        break
        link = urljoin(source_url, link) if link else ""
        desc = strip_html(_first_text(entry, {"summary", "content", "subtitle"}))
        pub_raw = _first_text(entry, {"published", "issued"}).strip()
        pub = _parse_datetime(pub_raw)
        upd = _parse_datetime(_first_text(entry, {"updated", "modified"}))
        if not title and not link:
            return None
        return {
            "title": title,
            "url": link,
            "description": desc[:_MAX_EXCERPT],
            "published_at_utc": pub or upd,
            "updated_at_utc": upd,
            # 元の公開日時文字列（日付品質ガード用）。
            "published_raw": pub_raw,
        }
    except Exception:  # noqa: BLE001
        return None


def parse_feed(content, source_url: str = "", max_items: int = 200) -> List[dict]:
    """RSS 2.0 / Atom を解析し、正規化前の生レコード(dict)のリストを返す。

    malformed feed（XML解析不能）や未対応ルート要素なら空リスト（例外は投げない）。
    max_items で1フィードあたりの取得件数に安全上限を設ける（§8 初回の暴発防止）。
    """
    text = _decode(content)
    if not text or not text.strip():
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # 末尾の壊れ等に備え、`<?xml ...?>` 宣言を外して再試行
        try:
            stripped = re.sub(r"^\s*<\?xml[^>]*\?>", "", text).strip()
            root = ET.fromstring(stripped)
        except ET.ParseError:
            return []

    root_name = _localname(root.tag)
    items: List[dict] = []

    if root_name == "rss":
        channel = None
        for child in list(root):
            if _localname(child.tag) == "channel":
                channel = child
                break
        container = channel if channel is not None else root
        source = strip_html(_first_text(container, {"title"})) if channel is not None else ""
        for elem in container.iter():
            if _localname(elem.tag) == "item":
                rec = _rss_item(elem, source_url)
                if rec:
                    rec["source"] = source
                    items.append(rec)
                    if len(items) >= max_items:
                        break
    elif root_name in ("feed", "rdf", "RDF"):
        # Atom (feed) または RSS 1.0 (RDF)
        source = strip_html(_first_text(root, {"title"}))
        entry_tags = {"entry", "item"}
        for elem in root.iter():
            if _localname(elem.tag) in entry_tags:
                if _localname(elem.tag) == "entry":
                    rec = _atom_entry(elem, source_url)
                else:
                    rec = _rss_item(elem, source_url)
                if rec:
                    rec["source"] = source
                    items.append(rec)
                    if len(items) >= max_items:
                        break
    else:
        return []

    return items


def detect_format(content) -> str:
    """フィード形式を判定する（'rss' / 'atom' / 'unknown'）。"""
    text = _decode(content)
    if not text:
        return "unknown"
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "unknown"
    name = _localname(root.tag)
    if name == "rss" or name in ("rdf", "RDF"):
        return "rss"
    if name == "feed":
        return "atom"
    return "unknown"
