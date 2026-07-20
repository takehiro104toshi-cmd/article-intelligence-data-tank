# CHANGELOG

## v0.5.2 (2026-07-20) — scheduled実行の間引き対策（1時間に4回へ冗長化）

v0.5.1で0分→17分にずらしたところ、17分の枠は実際に発火するようになった（22:17の
実行を確認）。しかし1本のcronだけでは依然として数時間おきにしか発火せず、GitHubの
scheduled workflowがベストエフォート（高負荷時にドロップ）である根本制約に当たって
いることが分かった。設定ミスではなくGitHub側の仕様。

### 修正

- `.github/workflows/article-tank-update.yml`: cronを`"17 * * * *"`（1時間1回）から
  `"8,23,38,53 * * * *"`（1時間4回・:08/:23/:38/:53）へ冗長化。GitHubが一部を
  間引いても「1時間にどれか1つは通る」確率を上げる。丸い分（:00/:15/:30/:45）は
  混雑するため避けた。
  ※ ETag/304（変更なしは即終了）＋「変更が無ければコミットしない」実装が既にあるため、
  余分な発火のコスト・commitはほぼ増えない（Publicリポジトリのため実行時間も無料）。

### 補足（GitHubスケジューラの限界）

冗長化しても「毎時必ず」は保証できない（GitHub公式が明記する制約）。確実な定時実行が
必要になった場合は、外部のスケジューラ（cron-job.org等）から workflow_dispatch を叩く、
またはセルフホストランナーの導入を検討する。現状は無料・低コストで実用上十分な頻度を
狙う方針。

## v0.5.1 (2026-07-20) — 毎時0分の混雑によるscheduled実行のスキップを回避

実行履歴を確認したところ、`cron: "0 * * * *"`（毎時0分）の自動実行が何度も
記録に残らず、数時間空くことがあった。GitHub公式ドキュメントに、scheduled
workflowは毎時0分（世界中のワークフローが集中する最混雑タイミング）に設定すると
遅延・スキップされやすいと明記されており、これが原因と判断した。

### 修正

- `.github/workflows/article-tank-update.yml`: cronを`"0 * * * *"`から
  `"17 * * * *"`（毎時17分）に変更。0分を避けることでスケジュール実行の
  信頼性を上げる（GitHub公式の推奨に準拠）。

## v0.5.0 (2026-07-20) — 記事スコアリングの実装（importance全0.00の修正）＋テーマ集計の整理

ライブ運用のレポートで「主要因のimportanceが全て0.00」「無関係な単発記事が主要因に
混入」「テーマ集計の最上位がuncategorized 614件」という問題が確認された。根本原因は、
ingestionがfreshness/source_trustしか設定しておらず、importance_score /
market_impact_score / urgency_score / structural_score が一度も計算されていなかった
こと（cluster集計はmax(記事スコア)のため全て0となり、global_driversの順位が無意味に
なっていた）。

### 修正

- `src/tank/scoring.py`: `score_article_signals(article)`を追加。分類結果
  （primary_category/themes）・情報源信頼度・緊急性キーワード（日英）のみから
  4スコアを決定論的に算出する（生成AI・外部データなし）。構造的カテゴリ・
  高市場影響カテゴリは金融政策・地政学・資源・防衛・半導体・AI等を同列に列挙
  （§17 公平性: 特定テーマを優遇するコードパスなし）。
- `src/tank/ingestion.py`: classify直後に`score_article_signals`を呼ぶよう配線。
  以降の新規記事はスコア付きで保存され、cluster集計・global_drivers順位・
  配信先（daily-market-brief）の重要度加点が実際に機能する。
  ※過去に保存済みの記事（スコア0のまま）は再計算しないが、hot window（72時間）
  経過後は自然に新スコア付き記事だけが配信対象になる。
- `src/tank/publication.py`: `build_theme_summary`から"uncategorized"を除外
  （分類できなかった印はテーマではないため、集計最上位に出るノイズを防ぐ）。
- `tests/test_scoring.py`: スコア算出（カテゴリ別の高低・緊急キーワード・0-1境界）と
  theme_summaryのuncategorized除外を検証するテストを4件追加。

### pytest

118 passed（既存114＋新規4）。

## v0.4.1 (2026-07-20) — degraded終了時にcommitステップがスキップされる不具合を修正

ソース拡充後の初回ライブ実行で発覚。`run_ingestion.py`の終了コード2（degraded:
一部ソース失敗だが配信Packageは公開成功）は、GitHub Actionsのステップとしては
「失敗」扱いになり、後続の「commit and push」ステップがデフォルトでスキップされて
いた。結果、パッケージ自体は正しく生成されているのに、shards・published/latest
への変更がリポジトリへ一切コミットされず失われる状態になっていた。

### 修正

- `.github/workflows/article-tank-update.yml`: 「Commit and push tank data +
  published package」ステップに`if: always()`を追加し、`run_ingestion.py`が
  degraded（終了コード2）で終わっても必ずcommit/pushを試みるようにした。
  全滅（終了コード1）の場合は新規シャード・Package自体が生成されないため
  `git diff`が空になり、従来通り自然にコミットはスキップされる（安全）。

## v0.4.0 (2026-07-20) — 英語記事の分類修正＋ソース拡充（重要度・業種・業界での精査を実効化）

「重要度・業種・業界に応じて精査」が実際には機能していなかった根本原因を修正した。
`DEFAULT_CATEGORY_KEYWORDS`が日本語キーワードのみで構成されていたため、Tankの
情報源の大半を占める英語記事（Fed/BBC/CNBC/NPR/Al Jazeera等）はほぼ`uncategorized`
になるか、たまたまASCIIの略語（AI/EV/SOX等）が英文中に部分一致した場合のみ
意図しないカテゴリに誤分類されていた。これを修正した上で、参照ソースを26→48件へ
拡充した（1日あたりの取り込み件数を増やし、より多くの候補から重要度・多様性で
選別できるようにするため）。

### 修正

- `src/tank/classify.py`: `DEFAULT_CATEGORY_KEYWORDS`の全カテゴリへ英語キーワードを
  追加（日英併記）。あわせて`_keyword_matches()`を追加し、英字キーワードは
  大文字小文字を無視した単語境界つき正規表現判定に変更（例: "AI"が"said"に含まれる
  "ai"へ誤反応しない）。日本語キーワードはスペース無しの連続文字列のため、
  従来通りの単純部分一致を維持（挙動を変えていない）。
- `tests/test_classify.py`: 英語記事の分類（AI/半導体/自動車/地政学）と、
  単語境界による誤検知防止（"said"→ai、"even"/"review"→EVに誤反応しないこと）を
  検証するテストを5件追加。

### 追加

- `config/sources.yaml`: 総合金融メディア（Reuters Business/Bloomberg Markets/
  WSJ Markets/MarketWatch/Yahoo Finance US）、地域分散（インド・アジア太平洋・
  カナダ・ドイツ・アフリカ・中南米）、業界専門メディア（小売/銀行/電力/医療/
  製造/物流/資源/通信/環境のDive系・専門媒体）、追加の中央銀行・公的統計
  （RBA・UK ONS）を追加。26ソース（enabled 17）→48ソース（enabled 34）。
  到達性未確認のものは`enabled: false`のまま（§6に従い推測で有効化しない）。

### 既知の注意点

- 新規追加分は本ビルド環境（egress制限あり）で到達性を確認できていない。
  必ず`python scripts/run_ingestion.py --verify`で確認し、FAILしたものは
  `enabled: false`にすること。
- 1日あたりの取り込み件数は情報源ごとの実際の更新頻度に依存するため、
  ソース数を増やしても目標値（例: 1日1000件）に届くとは限らない。実際の
  `--verify`・ライブ実行結果を見ながら追加調整する運用を想定している。

### pytest

114 passed（既存109＋新規5）。

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
