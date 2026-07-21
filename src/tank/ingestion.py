"""増分取得オーケストレーション（§2, §10, §25）。

fetch_fn は「1ソース分の生記事リストを返す関数」として外部から注入する
(source_config, cursor) -> List[dict]。本モジュール自体は実際のHTTP取得を
行わない（RSS/公開APIの実装は利用者がconfig.yamlのsourcesへURLを追加し、
scripts/run_ingestion.py 側の実フェッチャーで呼び出す。テストではフェイク関数を渡す）。

新着0件のソースは即座にスキップし（§25 高速終了）、既存記事は再分類しない
（dedupで弾かれるため classify は新規記事にしか呼ばれない）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from .classify import classify_article
from .cluster import find_matching_cluster, new_cluster_id, upsert_cluster
from .cursor import CursorStore, fetch_from_datetime
from .date_quality import sanitize_published_at
from .dedup import compute_hashes, find_duplicate_group
from .index import ArticleIndex
from .models import Article, EventCluster, SourceCursor
from .scoring import freshness_score, score_article_signals
from .storage import ArticleStore
from .url_normalize import normalize_url, source_domain_of


def _jst_date(dt: datetime) -> str:
    from datetime import timedelta
    jst = dt.astimezone(timezone(timedelta(hours=9)))
    return jst.strftime("%Y-%m-%d")


def build_article_from_raw(raw: dict, source_cfg: dict, ingestion_run_id: str, now: datetime) -> Article:
    """RSS/APIの生レコード(dict)から Article を組み立てる（正規化・ハッシュ計算込み）。"""
    canonical = normalize_url(raw.get("url", ""))
    # 日付品質ガード（§7）: 未来/超過去/解析不能の published_at は記事を破棄せず
    # fetched_at へ補正し、date_inferred=True・元文字列(raw_published_at)を保持する。
    fetched_iso = now.isoformat()
    published_clean, date_inferred, raw_published_at, _date_anomaly = sanitize_published_at(
        parsed_iso=raw.get("published_at_utc", "") or "",
        raw_string=raw.get("published_raw", "") or "",
        fetched_at_utc=fetched_iso,
        now=now,
    )
    article = Article(
        canonical_url=canonical,
        normalized_url=canonical,
        source_name=source_cfg.get("name", raw.get("source", "")),
        source_domain=source_domain_of(canonical),
        source_type=source_cfg.get("type", "rss"),
        source_trust=float(source_cfg.get("trust", 0.5)),
        source_country=source_cfg.get("country", ""),
        language=source_cfg.get("language", "ja"),
        title_original=raw.get("title", ""),
        description=raw.get("description", ""),
        public_excerpt=(raw.get("description", "") or "")[:200],
        body_storage_type="public_excerpt",
        body_available=bool(raw.get("description")),
        rights_classification="public",
        published_at_utc=published_clean,
        fetched_at_utc=fetched_iso,
        first_seen_at=now.isoformat(),
        last_seen_at=now.isoformat(),
        date_inferred=date_inferred,
        raw_published_at=raw_published_at,
        ingestion_run_id=ingestion_run_id,
    )
    # Production News Sources Phase: source_country を記事の国エンティティへ反映する。
    # Event Cluster のマッチングはエンティティ重複を要件とするため、実フィードでも
    # 「同一国＋同一カテゴリ＋類似見出し」でクラスタが形成できるようにする最小の補完。
    if source_cfg.get("country"):
        article.countries = [source_cfg["country"]]
    from datetime import timedelta
    # published_at_utc は日付品質ガードで補正済み（未来/超過去/欠損 → fetched_at）。
    # これにより異常な年(例:2008)のシャードへ記事が紛れ込み、retentionで
    # 最近の記事が誤削除されるのを防ぐ。
    article.published_at_jst = _jst_date(
        datetime.fromisoformat(article.published_at_utc.replace("Z", "+00:00"))
        if article.published_at_utc else now
    ) if article.published_at_utc else ""
    article.fetched_at_jst = now.astimezone(timezone(timedelta(hours=9))).isoformat()
    article.article_id = "art_" + compute_hashes(article).canonical_hash[:24]
    compute_hashes(article)
    return article


def run_ingestion_for_source(
    source_cfg: dict,
    fetch_fn: Callable[[dict, SourceCursor], List[dict]],
    store: ArticleStore,
    index: ArticleIndex,
    cursor_store: CursorStore,
    clusters: Dict[str, EventCluster],
    now: Optional[datetime] = None,
    overlap_hours: int = 48,
) -> dict:
    """1ソース分の増分取得〜保存までを実行し、サマリdictを返す（§10, §25）。"""
    now = now or datetime.now(timezone.utc)
    source_name = source_cfg["name"]
    cursor = cursor_store.get(source_name)
    cursor.last_fetch_started_at = now.isoformat()

    fetch_from = fetch_from_datetime(cursor, overlap_hours=overlap_hours)
    raw_articles = fetch_fn(source_cfg, cursor)

    if not raw_articles:
        cursor.last_fetch_completed_at = now.isoformat()
        cursor_store.update(cursor)
        return {"source": source_name, "fetched": 0, "new": 0, "duplicates": 0}

    run_id = uuid.uuid4().hex[:12]
    # canonical_hash / content_hash は「完全重複(exact duplicate)」判定に使うため
    # 1つの辞書へまとめる。title_hash は「再配信記事(syndicated duplicate)」判定用に別辞書。
    exact_map: Dict[str, str] = {}
    exact_map.update(index.canonical_hash_map())
    exact_map.update(index.content_hash_map())
    title_map = index.title_hash_map()

    articles_by_date: Dict[str, List[Article]] = {}
    new_count = 0
    dup_count = 0
    date_inferred_count = 0
    articles_by_id: Dict[str, Article] = {}

    for raw in raw_articles:
        article = build_article_from_raw(raw, source_cfg, run_id, now)
        dup_group = find_duplicate_group(article, exact_map, title_map)
        if dup_group is not None:
            dup_count += 1
            continue

        if article.date_inferred:
            date_inferred_count += 1
        classify_article(article)
        article.freshness_score = freshness_score(article.published_at_utc, now)
        article.source_score = article.source_trust
        # v0.5.0: importance/market_impact/urgency/structural を分類結果から機械的に算出。
        # これが無いと全記事0.0のままcluster集計・global_drivers順位・配信先の
        # 重要度加点がすべて無意味になる（classify後に呼ぶこと）。
        score_article_signals(article)

        cluster_id = find_matching_cluster(article, clusters, articles_by_id)
        if cluster_id is None:
            cluster_id = new_cluster_id(article)
        upsert_cluster(clusters, cluster_id, article, articles_by_id)
        articles_by_id[article.article_id] = article

        exact_map[article.canonical_hash] = article.duplicate_group_id or article.canonical_hash
        exact_map[article.content_hash] = article.duplicate_group_id or article.canonical_hash
        title_map[article.title_hash] = article.duplicate_group_id or article.canonical_hash

        date_key = article.published_at_jst or _jst_date(now)
        articles_by_date.setdefault(date_key, []).append(article)
        new_count += 1

    for date_key, articles in articles_by_date.items():
        store.append_articles(date_key, articles)
        index.upsert_articles(articles, shard_date=date_key)

    if new_count:
        newest = max(articles_by_id.values(), key=lambda a: a.published_at_utc or "")
        cursor.latest_published_at = newest.published_at_utc
        cursor.latest_article_id = newest.article_id
    cursor.last_fetch_completed_at = now.isoformat()
    cursor.consecutive_failures = 0
    cursor_store.update(cursor)

    return {"source": source_name, "fetched": len(raw_articles), "new": new_count,
            "duplicates": dup_count, "date_inferred": date_inferred_count}


def run_ingestion_all(
    sources: List[dict],
    fetch_fn: Callable[[dict, SourceCursor], List[dict]],
    store: ArticleStore,
    index: ArticleIndex,
    cursor_store: CursorStore,
    clusters: Dict[str, EventCluster],
    now: Optional[datetime] = None,
    overlap_hours: int = 48,
) -> List[dict]:
    now = now or datetime.now(timezone.utc)
    return [
        run_ingestion_for_source(src, fetch_fn, store, index, cursor_store, clusters, now, overlap_hours)
        for src in sources
    ]


# ---------- Production News Sources Phase: 実HTTP取得を伴うライブ取り込み ----------
# 既存の run_ingestion_for_source（dedup/classify/cluster/store）をそのまま再利用し、
# HTTP層（fetcher）と Cursor(ETag/Last-Modified/304/失敗)管理だけを上に薄く重ねる。


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def rebuild_index_from_store(store: ArticleStore, index: ArticleIndex,
                             date_from: Optional[str] = None) -> int:
    """コミット済みのシャード（テキスト）から SQLite 索引を再構築する。

    GitHub Actions のチェックアウトには索引(sqlite)が含まれない（.gitignore）。
    シャードは source of truth（追記型テキスト）なので、索引が空のときは
    シャードから重複排除用のハッシュを含む索引を復元し、実行間の増分・重複排除を成立させる。

    date_from（'YYYY-MM-DD'）を渡すと、その日以降のシャードだけを再構築対象にする
    （retention窓に限定して、いずれ削除される古いシャードまで毎回読み込む無駄を省く）。
    戻り値: 索引へ投入した記事数。
    """
    by_date: Dict[str, List[Article]] = {}
    for article in store.iter_shards(date_from=date_from):
        date_key = article.published_at_jst or (article.fetched_at_jst or "")[:10]
        by_date.setdefault(date_key, []).append(article)
    total = 0
    for date_key, articles in by_date.items():
        total += index.upsert_articles(articles, shard_date=date_key)
    return total


def _apply_fetch_result(
    source_cfg: dict,
    result,
    store: ArticleStore,
    index: ArticleIndex,
    cursor_store: CursorStore,
    clusters: Dict[str, EventCluster],
    now: datetime,
    overlap_hours: int = 48,
    retry_backoff_minutes: int = 30,
) -> dict:
    """取得済み FetchResult を保存・Index・Cursorへ反映する（**単一writerで直列に呼ぶ**）。

    HTTP取得(fetch_feed)は並列化できるが、この適用処理は shard write / SQLite index /
    Cursor更新を伴うため必ず単一スレッドから直列に呼ぶこと（§2, §3 thread safety）。
    """
    from datetime import timedelta

    name = source_cfg["name"]
    cursor = cursor_store.get(name)

    if result.status == "not_modified":
        cursor.last_fetch_completed_at = _iso(now)
        cursor.last_http_status = 304
        cursor.consecutive_failures = 0
        cursor.last_error = ""
        if result.etag:
            cursor.etag = result.etag
        if result.last_modified:
            cursor.last_modified = result.last_modified
        cursor_store.update(cursor)
        return {"source": name, "status": "unchanged", "http_status": 304,
                "fetched": 0, "new": 0, "duplicates": 0}

    if result.status == "failed":
        cursor.consecutive_failures = int(cursor.consecutive_failures) + 1
        cursor.last_http_status = result.http_status
        cursor.last_error = result.error
        cursor.next_retry_at = _iso(now + timedelta(minutes=retry_backoff_minutes))
        cursor.last_fetch_completed_at = _iso(now)
        cursor_store.update(cursor)
        return {"source": name, "status": "failed", "http_status": result.http_status,
                "fetched": 0, "new": 0, "duplicates": 0, "error": result.error}

    # ok: ETag/Last-Modified を先に永続化してから既存パイプラインへ委譲
    cursor.etag = result.etag or cursor.etag
    cursor.last_modified = result.last_modified or cursor.last_modified
    cursor.last_http_status = result.http_status
    cursor.last_error = ""
    cursor_store.update(cursor)

    summary = run_ingestion_for_source(
        source_cfg, lambda cfg, cur: result.articles,
        store, index, cursor_store, clusters, now, overlap_hours,
    )
    cur2 = cursor_store.get(name)
    cur2.last_success_count = summary.get("new", 0)
    cursor_store.update(cur2)

    summary["status"] = "success"
    summary["http_status"] = result.http_status
    return summary


def run_live_ingestion_for_source(
    source_cfg: dict,
    store: ArticleStore,
    index: ArticleIndex,
    cursor_store: CursorStore,
    clusters: Dict[str, EventCluster],
    now: Optional[datetime] = None,
    overlap_hours: int = 48,
    timeout: int = 12,
    retry: int = 1,
    transport=None,
    max_items: int = 200,
    retry_backoff_minutes: int = 30,
) -> dict:
    """1ソースを実際にHTTP取得し、304/失敗/成功に応じてCursorを更新して取り込む（逐次版）。"""
    from .fetcher import fetch_feed

    now = now or datetime.now(timezone.utc)
    name = source_cfg["name"]
    cursor = cursor_store.get(name)
    cursor.last_fetch_started_at = _iso(now)

    result = fetch_feed(source_cfg, cursor, timeout=timeout, retry=retry,
                        transport=transport, max_items=max_items)
    return _apply_fetch_result(source_cfg, result, store, index, cursor_store,
                               clusters, now, overlap_hours, retry_backoff_minutes)


def _host_of(source_cfg: dict) -> str:
    """rate limit・同時接続制御のためにソースURLのホスト名を取り出す。"""
    from urllib.parse import urlparse
    try:
        return (urlparse(source_cfg.get("url", "")).netloc or "").lower()
    except ValueError:
        return ""


def run_live_ingestion_all(
    sources: List[dict],
    store: ArticleStore,
    index: ArticleIndex,
    cursor_store: CursorStore,
    clusters: Dict[str, EventCluster],
    now: Optional[datetime] = None,
    overlap_hours: int = 48,
    timeout: int = 12,
    retry: int = 1,
    transport=None,
    max_items: int = 200,
    logger=None,
    max_workers: int = 1,
    per_host_max: int = 2,
    user_agent: Optional[str] = None,
) -> List[dict]:
    """全ソースをライブ取得する（§2 並列取得）。

    HTTP取得(fetch_feed)だけを ThreadPoolExecutor で並列化し、保存・Index・Cursor更新は
    **メインスレッドで結果を Source ID 順に直列適用**する（SQLite/shard の単一writerを保証）。
    per_host_max で同一ホストへの同時接続数を制限する（アクセス過多防止）。
    max_workers<=1 のときは完全逐次（従来動作と同一）。1ソースの例外で全体を止めない。
    """
    from .fetcher import fetch_feed

    now = now or datetime.now(timezone.utc)
    # 適用順を決定的にする（スレッド完了順に依らない）。id が無ければ name をキーに。
    ordered = sorted(sources, key=lambda s: str(s.get("id") or s.get("name", "")))

    # ---- Phase 1: HTTP取得（並列可・書き込みなし）。cursorはfetch前に読むだけ。 ----
    def _fetch_one(src: dict):
        name = src["name"]
        cur = cursor_store.get(name)
        cur.last_fetch_started_at = _iso(now)  # 開始時刻の記録のみ（updateは適用時）
        try:
            return src, fetch_feed(src, cur, timeout=timeout, retry=retry,
                                   transport=transport, max_items=max_items,
                                   user_agent=user_agent)
        except Exception as exc:  # noqa: BLE001  取得の想定外例外もsource isolation
            from .fetcher import FetchResult
            return src, FetchResult(status="failed", http_status=0,
                                    error=f"{type(exc).__name__}: {str(exc)[:120]}")

    fetched: Dict[str, tuple] = {}
    if max_workers and max_workers > 1 and len(ordered) > 1:
        import threading
        from concurrent.futures import ThreadPoolExecutor

        host_sems: Dict[str, threading.Semaphore] = {}
        sem_lock = threading.Lock()

        def _fetch_guarded(src: dict):
            host = _host_of(src)
            with sem_lock:
                sem = host_sems.setdefault(host, threading.Semaphore(max(1, per_host_max)))
            with sem:  # 同一ホストへの同時接続数を制限
                return _fetch_one(src)

        with ThreadPoolExecutor(max_workers=int(max_workers)) as ex:
            for src, result in ex.map(_fetch_guarded, ordered):
                fetched[str(src.get("id") or src.get("name", ""))] = (src, result)
    else:
        for src in ordered:
            s, result = _fetch_one(src)
            fetched[str(s.get("id") or s.get("name", ""))] = (s, result)

    # ---- Phase 2: 適用（直列・決定的順序・単一writer） ----
    results: List[dict] = []
    for key in [str(s.get("id") or s.get("name", "")) for s in ordered]:
        src, result = fetched[key]
        try:
            res = _apply_fetch_result(src, result, store, index, cursor_store,
                                      clusters, now, overlap_hours)
        except Exception as exc:  # noqa: BLE001  適用の想定外例外でも全体を止めない
            res = {"source": src.get("name", "unknown"), "status": "failed",
                   "http_status": 0, "fetched": 0, "new": 0, "duplicates": 0,
                   "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        if logger:
            logger(res)
        results.append(res)
    return results
