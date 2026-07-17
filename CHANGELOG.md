# CHANGELOG

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
