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
        published_at_utc=raw.get("published_at_utc", now.isoformat()),
        fetched_at_utc=now.isoformat(),
        first_seen_at=now.isoformat(),
        last_seen_at=now.isoformat(),
        ingestion_run_id=ingestion_run_id,
    )
    # Production News Sources Phase: source_country を記事の国エンティティへ反映する。
    # Event Cluster のマッチングはエンティティ重複を要件とするため、実フィードでも
    # 「同一国＋同一カテゴリ＋類似見出し」でクラスタが形成できるようにする最小の補完。
    if source_cfg.get("country"):
        article.countries = [source_cfg["country"]]
    from datetime import timedelta
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
    articles_by_id: Dict[str, Article] = {}

    for raw in raw_articles:
        article = build_article_from_raw(raw, source_cfg, run_id, now)
        dup_group = find_duplicate_group(article, exact_map, title_map)
        if dup_group is not None:
            dup_count += 1
            continue

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

    return {"source": source_name, "fetched": len(raw_articles), "new": new_count, "duplicates": dup_count}


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


def rebuild_index_from_store(store: ArticleStore, index: ArticleIndex) -> int:
    """コミット済みのシャード（テキスト）から SQLite 索引を再構築する。

    GitHub Actions のチェックアウトには索引(sqlite)が含まれない（.gitignore）。
    シャードは source of truth（追記型テキスト）なので、索引が空のときは
    シャードから重複排除用のハッシュを含む索引を復元し、実行間の増分・重複排除を成立させる。
    戻り値: 索引へ投入した記事数。
    """
    by_date: Dict[str, List[Article]] = {}
    for article in store.iter_shards():
        date_key = article.published_at_jst or (article.fetched_at_jst or "")[:10]
        by_date.setdefault(date_key, []).append(article)
    total = 0
    for date_key, articles in by_date.items():
        total += index.upsert_articles(articles, shard_date=date_key)
    return total


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
    """1ソースを実際にHTTP取得し、304/失敗/成功に応じてCursorを更新して取り込む。

    - not_modified(304): 記事を保存せず Cursor の最終取得時刻だけ更新（高速終了）。
    - failed: consecutive_failures を増やし、latest_published_at は進めない（§8）。
    - ok: ETag/Last-Modified を保存後、既存の run_ingestion_for_source へ委譲。
    """
    from datetime import timedelta

    from .fetcher import fetch_feed

    now = now or datetime.now(timezone.utc)
    name = source_cfg["name"]
    cursor = cursor_store.get(name)
    cursor.last_fetch_started_at = _iso(now)

    result = fetch_feed(source_cfg, cursor, timeout=timeout, retry=retry,
                        transport=transport, max_items=max_items)

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
        # latest_published_at / latest_article_id は進めない（取得失敗時に位置を進めない）
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
    # 成功件数を記録（run_ingestion_for_source が consecutive_failures=0 済み）
    cur2 = cursor_store.get(name)
    cur2.last_success_count = summary.get("new", 0)
    cursor_store.update(cur2)

    summary["status"] = "success"
    summary["http_status"] = result.http_status
    return summary


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
) -> List[dict]:
    """全ソースをライブ取得する。1ソースが予期せぬ例外を投げても他を継続する（source isolation §10）。"""
    now = now or datetime.now(timezone.utc)
    results: List[dict] = []
    for src in sources:
        try:
            res = run_live_ingestion_for_source(
                src, store, index, cursor_store, clusters, now,
                overlap_hours=overlap_hours, timeout=timeout, retry=retry,
                transport=transport, max_items=max_items,
            )
        except Exception as exc:  # noqa: BLE001  1ソースの想定外例外で全体を止めない
            res = {"source": src.get("name", "unknown"), "status": "failed",
                   "http_status": 0, "fetched": 0, "new": 0, "duplicates": 0,
                   "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        if logger:
            logger(res)
        results.append(res)
    return results
