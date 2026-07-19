"""RSS/Atom パーサのテスト（§14 Fetcher 1-11 のうち解析系）。"""
from tank.feed_parser import detect_format, parse_feed, strip_html

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Src</title>
  <item><title>Fed holds rates</title><link>https://ex.gov/a?utm_source=x</link>
    <description>&lt;p&gt;Policy &amp; inflation&lt;/p&gt;</description>
    <pubDate>Wed, 15 Jul 2026 18:00:00 GMT</pubDate></item>
  <item><title>Second</title><link>/relative/path</link>
    <description>Body</description></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Src</title>
  <entry><title>Oil rises</title><link rel="alternate" href="https://ex.gov/oil"/>
    <summary>OPEC supply</summary><published>2026-07-15T15:30:00Z</published></entry>
</feed>"""


def test_parse_rss_basic():
    items = parse_feed(RSS, source_url="https://ex.gov/feed.xml")
    assert len(items) == 2
    assert items[0]["title"] == "Fed holds rates"
    assert items[0]["published_at_utc"].startswith("2026-07-15")


def test_parse_atom_basic():
    items = parse_feed(ATOM, source_url="https://ex.gov/feed.xml")
    assert len(items) == 1
    assert items[0]["title"] == "Oil rises"
    assert items[0]["url"] == "https://ex.gov/oil"
    assert items[0]["published_at_utc"].startswith("2026-07-15T15:30")


def test_atom_namespace_handled():
    # 名前空間つきタグ（{http://www.w3.org/2005/Atom}entry）でも解析できる
    assert detect_format(ATOM) == "atom"
    assert len(parse_feed(ATOM)) == 1


def test_html_entities_and_tags_stripped():
    items = parse_feed(RSS)
    # &lt;p&gt;Policy &amp; inflation&lt;/p&gt; → "Policy & inflation"（タグ除去・実体復号）
    assert "<p>" not in items[0]["description"]
    assert "Policy & inflation" in items[0]["description"]


def test_relative_url_resolved():
    items = parse_feed(RSS, source_url="https://ex.gov/news/feed.xml")
    assert items[1]["url"] == "https://ex.gov/relative/path"


def test_tracking_param_present_in_raw_url_but_normalized_later():
    # パーサ段階では素のURLを返す（正規化は ingestion 側の normalize_url が担当）
    items = parse_feed(RSS)
    assert items[0]["url"].startswith("https://ex.gov/a")


def test_missing_published_is_empty_not_guessed():
    items = parse_feed(RSS)
    assert items[1]["published_at_utc"] == ""  # 日時欠損は推測しない


def test_malformed_item_skipped_feed_continues():
    feed = b"""<rss version="2.0"><channel>
      <item><title></title><link></link></item>
      <item><title>Valid</title><link>https://ex/1</link></item>
    </channel></rss>"""
    items = parse_feed(feed)
    assert len(items) == 1 and items[0]["title"] == "Valid"


def test_malformed_feed_returns_empty_no_exception():
    assert parse_feed(b"<rss><channel><item><title>broken") == []
    assert parse_feed(b"not xml at all") == []
    assert parse_feed(b"") == []


def test_non_utf8_encoding_decoded():
    feed = "<?xml version='1.0' encoding='ISO-8859-1'?><rss version='2.0'><channel>\
<item><title>Caf\xe9 news</title><link>https://ex/1</link></item></channel></rss>".encode("latin-1")
    items = parse_feed(feed)
    assert len(items) == 1
    assert "Caf" in items[0]["title"]


def test_max_items_limit():
    body = "<rss version='2.0'><channel>" + "".join(
        f"<item><title>T{i}</title><link>https://ex/{i}</link></item>" for i in range(50)
    ) + "</channel></rss>"
    items = parse_feed(body.encode("utf-8"), max_items=10)
    assert len(items) == 10


def test_strip_html_helper():
    assert strip_html("<b>Hi</b> &amp; bye") == "Hi & bye"
    assert strip_html(None) == ""


def test_rss1_rdf_parsed_as_items():
    rdf = b"""<?xml version="1.0"?>
    <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
             xmlns="http://purl.org/rss/1.0/">
      <item><title>RDF item</title><link>https://ex/rdf1</link></item>
    </rdf:RDF>"""
    items = parse_feed(rdf)
    assert len(items) == 1 and items[0]["title"] == "RDF item"
