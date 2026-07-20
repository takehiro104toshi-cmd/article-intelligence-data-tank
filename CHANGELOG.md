# CHANGELOG

## v0.3.0 (2026-07-20) — Retention（保存データの保持期間・自動削除）

ArticleStore/ArticleIndexは追記オンリー（append-only）で、記事は取得され続ける限り
無期限に蓄積されていた（`article_tank.retention_days`はconfig.yamlに存在したが未配線）。
GitHub repoの無制限な肥大化と、極端に古い記事が配信候補に紛れ込むリスクを防ぐため、
保持期間を過ぎたシャード・索引行を毎回の実行時に自動削除する仕組みを配線した。

### 追加

- `src/tank/storage.py`: `ArticleStore.purge_shards_before(cutoff_date)`を追加。
  cutoff_dateより古い日付のシャードファイルを削除し、削除した日付一覧を返す。
- `src/tank/index.py`: `ArticleIndex.delete_before(cutoff_date)`を追加。
  shard_dateがcutoff_dateより古い行をSQLiteから削除する（shard_date未設定の行は
  誤削除しないよう対象外）。
- `scripts/run_ingestion.py`: 取得処理の直後に`article_tank.retention_days`
  （既定30日）を読み、cutoff（JST日付）より古いシャード・索引行を削除する処理を
  追加。`retention_days: null`にすると従来通りの無期限保持へ戻せる（後方互換）。
- `config.yaml`: `retention_days: null`（未配線・無期限）を`retention_days: 30`
  （配線済み・30日）に変更。
- `tests/test_storage.py` / `tests/test_index.py`: purge_shards_before /
  delete_before の境界値・no-op・shard_date未設定行の非削除を検証するテストを
  4件追加。

### pytest

109 passed（既存105＋新規4）。

## v0.2.0 (2026-07-18) — Production News Sources & Live Ingestion Phase

現在空だった Data Tank へ、実在する公開RSS/Atomから実際にニュースを取得する
ライブ取り込みパイプラインを実装。既存の Storage / Index / Cursor / Dedup /
Cluster / Scoring / Publication をそのまま再利用し、その上に HTTP 取得層と
ソース設定を薄く重ねた（大規模リファクタなし）。既存68テストは全通過。

### 追加

- `src/tank/feed_parser.py`【新規】: RSS 2.0 / Atom / RSS1.0(RDF) パーサ（stdlib
  xml.etree のみ・依存追加なし）。XML namespace / HTMLエンティティ・タグ除去 /
  相対URL解決 / RFC822・RFC3339日時のUTC正規化 / 日時欠損の非推測 / malformed
  item スキップ / malformed feed は空リスト / 非UTF-8デコード / 取得件数上限。
- `src/tank/fetcher.py`【新規】: HTTP条件付きGET Fetcher。ETag(If-None-Match) /
  Last-Modified(If-Modified-Since) / 304高速終了 / 403・404・429は再試行しない /
  5xx・network error・timeoutは retry 対象 / gzip・redirectは requests が処理 /
  例外を投げず FetchResult を返す（source isolation）。file:// もサポート
  （ローカル/エアギャップ・テスト用フィード。認証情報は一切使わない）。
- `src/tank/source_config.py`【新規】: config/sources.yaml と config.yaml の
  sources: を読み込み・正規化（trust_score↔trust 変換、後方互換）。enabled抽出。
- `src/tank/run_stats.py`【新規】: run統計（§13の全項目）を組み立て・atomic保存。
  Secret/Token/内部スタックトレースは出さない。
- `config/sources.yaml`【新規】: 初期ニュースソース（26件・多地域/多カテゴリ）。
  AI・半導体へ偏らせない構成。中央銀行/政府統計/国際機関/主要メディア/資源/防衛/
  科学/テック等。URLの到達性はビルド環境のegress制限で未検証のため、
  `--verify` で確認してから有効化する運用（不確実なものは enabled: false）。
- `tests/test_feed_parser.py` / `test_fetcher.py` / `test_live_ingestion.py`【新規】:
  36件（RSS/Atom/namespace/entity/malformed/非UTF8/304/ETag/403/429/500/timeout/
  retry/source isolation/増分/Cursor非前進/全失敗/source_config/run_stats/
  private非露出/認証情報非付与）。

### 改善（既存ファイル・最小差分・後方互換）

- `src/tank/ingestion.py`: `run_live_ingestion_for_source` / `run_live_ingestion_all`
  を追加（既存 run_ingestion_for_source を再利用し、HTTP層とCursor管理だけを上に重ねる）。
  source isolation・304スキップ・失敗時にCursorを前進させない。build_article_from_raw で
  source_country を記事の国エンティティへ反映（実フィードで Event Cluster が形成できる最小補完）。
- `src/tank/models.py`: SourceCursor へ last_success_count / last_error を追加（後方互換）。
- `scripts/run_ingestion.py`: 実Fetcher配線／配信Package生成の不整合を修正（従来は
  articles=[], clusters={} で空Package生成だった）→ hot window の実記事＋本runのクラスタから
  Package生成。`--verify`（到達性確認のみ）追加、run統計保存、全ソース失敗時は既存Package
  を上書き保護、終了コード方針（0成功/2 degraded/1失敗）を明確化。
- `config.yaml`: sources_file（config/sources.yaml）と max_items_per_source を追加。

### 検証（ローカルend-to-end・fixtureフィードを file:// で実行）

- 初回: 2ソース→4記事取得（malformed item は自動スキップ）→分類→4クラスタ→保存→
  実データからPackage生成（0.066s）。ai_semiconductor_share=0.0（偏りなし）。
- 2回目: 4取得/新規0/exact_duplicate 4（再保存なし）・より高速（0.038s）・Cursor機能。
- 304 fast-path / --verify / 全ソース失敗時のPackage保護（checksum不変）を確認。

### pytest

104 passed（既存68＋新規36）。

## v0.1.0 (2026-07-17) — Article Intelligence Data Tank 初版（Phase A-C）

Market Intelligence System（daily-market-brief）とは独立した新規プロジェクトとして作成。
記事の取得・正規化・重複排除・分類・イベント統合・保存・配信パッケージ生成の基盤一式を実装。

### 追加

- `src/tank/models.py`: Article / EventCluster / SourceCursor スキーマ、Market Reaction Schema stub
- `src/tank/url_normalize.py`: URL正規化・トラッキングパラメータ除去
- `src/tank/dedup.py`: exact duplicate / syndicated duplicate 判定、article_id安定生成
- `src/tank/storage.py`: JSONLシャード保存（atomic write・quarantine・ジェネレータ読込）
- `src/tank/index.py`: SQLiteによる横断検索（日付/カテゴリ/国/企業/資源/地政学エンティティ/ソース信頼度）
- `src/tank/cursor.py`: ソース別cursor（overlap_hours考慮の増分取得・リトライ制御）
- `src/tank/classify.py`: キーワードベースの機械的分類（AI/半導体を優遇しない公平な設計）
- `src/tank/cluster.py`: Event Clustering（カテゴリ+エンティティ+時間近接+タイトル語彙重複）
- `src/tank/scoring.py`: Retrieval Score（relevance25%/market_reaction25%/freshness15%/
  source_trust10%/urgency10%/structural10%/independent_source_confirmation5%）
- `src/tank/diversity.py`: 多様性制御（source share/theme share/event cluster上限）
- `src/tank/market_reaction.py`: 市場反応スキーマ（新着から記録開始、全件backfillは不要）
- `src/tank/historical.py`: 過去記事の軽量関連検索（全文を渡さない）
- `src/tank/private_store.py`: private記事（有料記事等）専用ストレージ（公開物に絶対混入しない構造）
- `src/tank/quality.py`: tank_status / quality（偏り監視。AI・半導体比率は監視のみで優遇はしない）
- `src/tank/publication.py`: Published Intelligence Package生成（allowlist方式・件数上限・
  サイズ上限・gzip・checksum・atomic publish・manifest）
- `src/tank/adapters/`: PublicationAdapter（Local/GitHubPages実装、GitHubRelease/ObjectStorageはスタブ）
- `scripts/run_ingestion.py`: 増分取得〜配信パッケージ生成のCLI
- `.github/workflows/article-tank-update.yml`: 独立ワークフロー（1日6回、Market Intelligence
  より15分前に更新、workflow_dispatch対応）
- `tests/`: pytest 68件（schema/dedup/cluster/index検索/scoring/diversity/publication/
  performance/ingestion end-to-end）

### 設計方針

- Parquet/DuckDBではなく標準ライブラリ（sqlite3/json/gzip）でstorage_backend
  "local_sharded"を実装（依頼書が許容する代替構成。将来アダプタ差し替えで移行可能）。
- Published Packageは allowlist方式で組み立て、private/restricted本文・raw全文は
  構造的に混入できない。
- Market Reaction First（§18）: ニュース件数でなく実際の市場反応を優先する
  retrieval scoreで配信候補を選定。

### pytest

68 passed
