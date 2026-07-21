"""日付品質ガード（Production Stabilization §7）。

RSS/Atom フィードの published_at には、実運用で以下の異常がしばしば混じる。

  - 未来日（feedの時刻ずれ・タイムゾーン誤り）
  - 極端な過去日（2008年など。channelの古いpubDateやguid由来の誤り）
  - 解析不能（欠損・不正フォーマット）

これらを「無検証で信用」すると、記事が誤った日付のシャードへ保存され、
retention（保持期間）で最近の記事が丸ごと誤削除される（＝静かなデータ損失）。

本モジュールは記事を **破棄せず**、published_at を fetched_at へ補正し、
`date_inferred=True` を立て、元の公開日時文字列を `raw_published_at` として
保持する（後から検証可能にする）。勝手な既定日は設定しない（fetched_at が
無い場合のみ now を使う）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

DEFAULT_MAX_FUTURE_HOURS = 24
DEFAULT_MAX_AGE_YEARS = 20


def _parse_iso(value: str) -> Optional[datetime]:
    """ISO8601（末尾Z対応）を aware datetime へ。失敗時 None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def sanitize_published_at(
    parsed_iso: str,
    raw_string: str,
    fetched_at_utc: str,
    now: Optional[datetime] = None,
    max_future_hours: int = DEFAULT_MAX_FUTURE_HOURS,
    max_age_years: int = DEFAULT_MAX_AGE_YEARS,
) -> Tuple[str, bool, str, str]:
    """published_at の妥当性を検査し、必要なら fetched_at へ補正する。

    引数:
        parsed_iso: feed_parser が正規化した ISO8601（解析不能なら空文字）。
        raw_string: フィードの元の公開日時文字列（保持用。無ければ空文字）。
        fetched_at_utc: 取得時刻（補正先。ISO8601）。
        now: 現在時刻（未来判定の基準。既定は now(UTC)）。

    戻り値:
        (published_at_utc, date_inferred, raw_published_at, anomaly)
        anomaly は "" / "missing" / "unparsable" / "future" / "too_old"。
    """
    now = now or datetime.now(timezone.utc)
    # 元文字列は常に保持（parsed_iso しか無い場合はそれを控えとして残す）。
    raw_published_at = (raw_string or parsed_iso or "").strip()
    fetched_dt = _parse_iso(fetched_at_utc)
    fallback = fetched_dt.isoformat() if fetched_dt else now.isoformat()

    if not parsed_iso:
        # 欠損 or 解析不能: 破棄せず fetched_at を採用。
        anomaly = "missing" if not raw_string else "unparsable"
        return fallback, True, raw_published_at, anomaly

    dt = _parse_iso(parsed_iso)
    if dt is None:
        return fallback, True, raw_published_at, "unparsable"

    if dt > now + timedelta(hours=max_future_hours):
        return fallback, True, raw_published_at, "future"

    # 通常ニュースfeedで max_age_years 年以上前は異常とみなす（記事は保持、日付だけ補正）。
    if dt < now - timedelta(days=365 * max_age_years):
        return fallback, True, raw_published_at, "too_old"

    # 正常: 解析済みの値をそのまま使う（元文字列も保持）。
    return dt.isoformat(), False, raw_published_at, ""
