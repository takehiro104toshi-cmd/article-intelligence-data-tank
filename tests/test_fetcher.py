"""RSS/Atom Fetcher のテスト（§14 Fetcher 12-20）。ネットワーク無し・transport注入。"""
from tank.fetcher import USER_AGENT, fetch_feed
from tank.models import SourceCursor

RSS = b"""<rss version="2.0"><channel><title>S</title>
  <item><title>A</title><link>https://ex/1</link><description>d</description>
  <pubDate>Wed, 15 Jul 2026 18:00:00 GMT</pubDate></item></channel></rss>"""


def _transport(status, headers=None, body=b"", record=None):
    def t(url, hdrs, timeout):
        if record is not None:
            record["url"] = url
            record["headers"] = hdrs
            record["timeout"] = timeout
        return status, headers or {}, body
    return t


def test_ok_parses_articles_and_captures_validators():
    rec = {}
    r = fetch_feed({"url": "https://ex/feed"}, None,
                   transport=_transport(200, {"ETag": '"v1"', "Last-Modified": "Wed, 15 Jul 2026 18:00:00 GMT"}, RSS, rec))
    assert r.status == "ok" and len(r.articles) == 1
    assert r.etag == '"v1"' and r.last_modified.startswith("Wed")
    # 適切なヘッダを送っている（User-Agent / Accept / Accept-Encoding）
    assert rec["headers"]["User-Agent"] == USER_AGENT
    assert "Accept" in rec["headers"] and "gzip" in rec["headers"]["Accept-Encoding"]


def test_conditional_headers_sent_from_cursor():
    rec = {}
    cur = SourceCursor(etag='"v1"', last_modified="Wed, 15 Jul 2026 18:00:00 GMT")
    fetch_feed({"url": "https://ex/feed"}, cur, transport=_transport(304, {}, b"", rec))
    assert rec["headers"]["If-None-Match"] == '"v1"'
    assert rec["headers"]["If-Modified-Since"].startswith("Wed")


def test_304_not_modified():
    cur = SourceCursor(etag='"v1"')
    r = fetch_feed({"url": "https://ex/feed"}, cur, transport=_transport(304, {"ETag": '"v1"'}))
    assert r.status == "not_modified" and r.http_status == 304 and r.articles == []


def test_403_failed_no_retry():
    calls = {"n": 0}

    def t(url, h, to):
        calls["n"] += 1
        return 403, {}, b""

    r = fetch_feed({"url": "https://ex/feed"}, None, retry=3, transport=t)
    assert r.status == "failed" and r.http_status == 403
    assert calls["n"] == 1  # 403は再試行しない（§3）


def test_429_failed_no_retry():
    calls = {"n": 0}

    def t(url, h, to):
        calls["n"] += 1
        return 429, {}, b""

    r = fetch_feed({"url": "https://ex/feed"}, None, retry=3, transport=t)
    assert r.status == "failed" and r.http_status == 429
    assert calls["n"] == 1  # 429は再試行しない（§3）


def test_404_failed():
    r = fetch_feed({"url": "https://ex/feed"}, None, transport=_transport(404))
    assert r.status == "failed" and r.http_status == 404


def test_500_retried_then_failed():
    calls = {"n": 0}

    def t(url, h, to):
        calls["n"] += 1
        return 500, {}, b""

    r = fetch_feed({"url": "https://ex/feed"}, None, retry=2, transport=t)
    assert r.status == "failed" and r.http_status == 500
    assert calls["n"] == 3  # 5xxは retry 対象（初回+2）


def test_timeout_network_error_isolated_and_retried():
    calls = {"n": 0}

    def t(url, h, to):
        calls["n"] += 1
        raise TimeoutError("timed out")

    r = fetch_feed({"url": "https://ex/feed"}, None, retry=1, transport=t)
    assert r.status == "failed" and r.http_status == 0
    assert "TimeoutError" in r.error
    assert calls["n"] == 2  # network error は retry 対象（例外は投げない＝isolation）


def test_5xx_then_success_on_retry():
    seq = [(500, {}, b""), (200, {"ETag": '"e"'}, RSS)]

    def t(url, h, to):
        return seq.pop(0)

    r = fetch_feed({"url": "https://ex/feed"}, None, retry=1, transport=t)
    assert r.status == "ok" and len(r.articles) == 1


def test_no_url_fails_gracefully():
    r = fetch_feed({"url": ""}, None)
    assert r.status == "failed" and r.error == "no_url"
