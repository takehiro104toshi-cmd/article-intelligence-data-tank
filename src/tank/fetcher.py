"""RSS / Atom Fetcher（Production News Sources Phase §3）。

HTTP条件付きGET（ETag / If-Modified-Since）による増分取得、gzip/redirect対応、
timeout / retry、304・403・404・429・500 のハンドリング、network failure時の
source isolation を提供する。実HTTPは requests（既存依存）を使う。テストでは
transport（callable）を注入してネットワーク無しで検証する。

方針（§3）:
  - 429 や明確なアクセス拒否（403/404）に対しては再試行しない（無理な回避もしない）。
  - 再試行するのは network error / timeout / 5xx のみ（retry回数分）。
  - 全文取得はしない。フィード本文のみ取得し、feed_parser で正規化前レコードへ。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .feed_parser import parse_feed
from .models import SourceCursor

USER_AGENT = "ArticleIntelligenceDataTank/1.0 (+public-rss-reader; contact: local)"
DEFAULT_TIMEOUT = 12
DEFAULT_RETRY = 1


def build_user_agent(name: str = "ArticleIntelligenceDataTank",
                     contact_email: str = "",
                     repo_url: str = "https://github.com/takehiro104toshi-cmd/article-intelligence-data-tank") -> str:
    """SEC等の「連絡先を含むUser-Agent」要件に沿ったUA文字列を組み立てる（§6）。

    SEC EDGAR のフェアアクセスポリシーは、リクエストに要求元を識別できる
    User-Agent（会社/アプリ名＋連絡先メール）を含めることを求めており、
    連絡先の無いUAは403で拒否されることがある。連絡先メールは環境変数/Secretで
    与える（コードへ個人アドレスを固定しない）。未設定時は連絡先なしの汎用UAを返す
    （呼び出し側でSEC等の厳格ソースを有効化しない判断に使う）。
    """
    name = (name or "ArticleIntelligenceDataTank").strip()
    email = (contact_email or "").strip()
    if email:
        return f"{name}/1.0 ({email}; +{repo_url})"
    return f"{name}/1.0 (+{repo_url}; contact: unset)"

# transport(url, headers, timeout) -> (status_code:int, headers:dict, body:bytes)
Transport = Callable[[str, dict, int], Tuple[int, dict, bytes]]

# 再試行しないHTTPステータス（明確なアクセス拒否・クライアント側の恒久的失敗）。§3
_NO_RETRY_STATUS = {403, 404, 401, 410, 429}


@dataclass
class FetchResult:
    status: str = "failed"          # "ok" / "not_modified" / "failed"
    articles: List[dict] = field(default_factory=list)
    etag: str = ""
    last_modified: str = ""
    http_status: int = 0
    error: str = ""
    final_url: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _requests_transport(url: str, headers: dict, timeout: int) -> Tuple[int, dict, bytes]:
    """既定のtransport。http(s)は requests（gzip・redirect自動）。file:// はローカル読み込み
    （ローカル/エアギャップ環境やテスト用フィードに対応。認証情報は一切使わない）。"""
    if url.startswith("file://"):
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        path = url2pathname(urlparse(url).path)
        with open(path, "rb") as f:
            return 200, {}, f.read()

    import requests

    resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    return resp.status_code, dict(resp.headers), resp.content


def _build_headers(cursor: Optional[SourceCursor], user_agent: Optional[str] = None) -> dict:
    headers = {
        "User-Agent": user_agent or USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
    if cursor:
        if cursor.etag:
            headers["If-None-Match"] = cursor.etag
        if cursor.last_modified:
            headers["If-Modified-Since"] = cursor.last_modified
    return headers


def fetch_feed(
    source_cfg: dict,
    cursor: Optional[SourceCursor] = None,
    timeout: int = DEFAULT_TIMEOUT,
    retry: int = DEFAULT_RETRY,
    transport: Optional[Transport] = None,
    max_items: int = 200,
    user_agent: Optional[str] = None,
) -> FetchResult:
    """1ソース分のフィードを取得・解析して FetchResult を返す。例外は投げない
    （ネットワーク障害・タイムアウトは status="failed" として返す＝source isolation）。

    user_agent を渡すとそのUAで取得する（SEC等の連絡先付きUA要件に対応。§6）。
    """
    url = source_cfg.get("url", "")
    if not url:
        return FetchResult(status="failed", error="no_url")

    transport = transport or _requests_transport
    headers = _build_headers(cursor, user_agent=user_agent)
    attempts = max(1, int(retry) + 1)
    last_error = ""
    last_status = 0

    for attempt in range(attempts):
        try:
            status_code, resp_headers, body = transport(url, headers, timeout)
        except Exception as exc:  # noqa: BLE001  network error / timeout 等
            last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            last_status = 0
            continue  # network error は retry 対象

        last_status = status_code
        resp_headers = {str(k).lower(): v for k, v in (resp_headers or {}).items()}
        etag = str(resp_headers.get("etag", "") or "")
        last_modified = str(resp_headers.get("last-modified", "") or "")

        if status_code == 304:
            return FetchResult(status="not_modified", http_status=304,
                               etag=etag or (cursor.etag if cursor else ""),
                               last_modified=last_modified or (cursor.last_modified if cursor else ""))

        if status_code in _NO_RETRY_STATUS:
            # 明確なアクセス拒否・恒久的失敗 → 再試行しない（§3）
            return FetchResult(status="failed", http_status=status_code,
                               error=f"http_{status_code}")

        if 200 <= status_code < 300:
            fmt = source_cfg.get("format", "auto")
            try:
                articles = parse_feed(body, source_url=url, max_items=max_items)
            except Exception as exc:  # noqa: BLE001  parser は通常例外を投げないが保険
                return FetchResult(status="failed", http_status=status_code,
                                   error=f"parse_error: {str(exc)[:100]}")
            _ = fmt  # format は将来の厳密判定用（現状 auto 判定は parser 側）
            return FetchResult(status="ok", articles=articles, etag=etag,
                               last_modified=last_modified, http_status=status_code)

        # 5xx などは retry 対象
        last_error = f"http_{status_code}"

    return FetchResult(status="failed", http_status=last_status, error=last_error or "fetch_failed")
