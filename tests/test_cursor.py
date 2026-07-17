"""§30-8: cursor更新・overlap window（§10）。"""
from datetime import datetime, timedelta, timezone

from tank.cursor import CursorStore, fetch_from_datetime, should_retry_now
from tank.models import SourceCursor


def test_cursor_update_and_roundtrip(tmp_path):
    store = CursorStore(str(tmp_path / "cursors.json"))
    cur = SourceCursor(source_name="rss_a", latest_published_at="2026-07-15T00:00:00+00:00")
    store.update(cur)
    loaded = store.get("rss_a")
    assert loaded.latest_published_at == "2026-07-15T00:00:00+00:00"


def test_cursor_for_unknown_source_returns_empty_cursor(tmp_path):
    store = CursorStore(str(tmp_path / "cursors.json"))
    cur = store.get("unknown")
    assert cur.source_name == "unknown"
    assert cur.latest_published_at == ""


def test_overlap_window_subtracts_configured_hours():
    cur = SourceCursor(latest_published_at="2026-07-15T12:00:00+00:00")
    from_dt = fetch_from_datetime(cur, overlap_hours=48)
    assert from_dt == datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def test_no_latest_published_returns_none():
    cur = SourceCursor()
    assert fetch_from_datetime(cur) is None


def test_should_retry_now_respects_next_retry_at():
    now = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    cur = SourceCursor(next_retry_at=(now + timedelta(hours=1)).isoformat())
    assert should_retry_now(cur, now) is False
    assert should_retry_now(cur, now + timedelta(hours=2)) is True
