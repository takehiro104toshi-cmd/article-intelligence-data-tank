# CHANGELOG

## v0.8.1 (2026-07-21) — Batch 1候補の到達性確認結果を反映（enabled 33→45）

GitHub Actions の `verify_candidates` モードで、Batch 1由来20件＋既存disabled 15件の
計35候補ソースの到達性を実確認した。結果は **到達可能12 / 失敗23**。

### 有効化（12件・`verify_status: verified_healthy`）

- ecb_press（欧州中央銀行）/ boe_news（イングランド銀行）/ wto_news（WTO）/
  allafrica_headlines / mercopress / freightwaves / greenbiz（既存disabled 15件の一部）
- jp_fsa_news（金融庁）/ us_sec_press（SEC press）/ us_census_economic（Census）/
  us_federal_register_energy（Federal Register）/ us_ftc_press（FTC）（Batch1新規追加分）

### 未解決（23件・enabled: false のまま維持）

- **HTTP 403（forbidden_403、7件）**: us_bls, imf_news, jp_meti_release, SEC EDGAR
  8-K/10-Q/10-K/6-K（4件）。EDGARはUser-Agentポリシーが別途必要な可能性があり要調査。
- **HTTP 404（unreachable、16件）**: uk_gov, us_treasury, reuters_business, lightreading,
  rba_media, uk_ons_releases, および日本の政府系9件（首相官邸/総務省統計局/内閣府/財務省/
  国交省/日銀統計/JPX/環境省）、us_bea_news, us_fda_press。日本の省庁RSSは軒並み404の
  ため、掲載URLが変更・廃止されている可能性が高い（正式なURL調査はBatch2以降で実施）。

各ソースへ `verify_status`（verified_healthy / unreachable / forbidden_403）と
確認日を記録し、`config/sources.yaml` から検証状況を追跡できるようにした。

### テスト

- `test_new_batch1_sources_are_disabled_pending` を実態（verify後）に合わせて
  `test_no_source_enabled_without_verification`（§6: 未検証ソースを有効化しない不変条件）
  と `test_batch1_candidates_all_resolved_no_pending`（pending解消の確認）へ置き換え。
- 158→**159 passed**。

### Coverage（参考値・enabled 45件時点）

tier1_share 27%（12%→改善・目標35%は未達）/ japan_share 7%（6%→改善・目標12%は未達）/
us_share 53% / top_region North America 44%（依然集中・要Batch2でのregion分散）。
日本の政府系一次情報の多くが404だったため、日本Coverageの本格改善はURL再調査後の
Batch2以降に持ち越し。

## v0.8.0 (2026-07-21) — Phase 3 Batch 1（並列取得＋日米一次情報＋dedup検証）

Source Portfolio拡張の第1弾。150〜250ソースへ拡張する前提となる**並列取得基盤**を
整備し、日本・米国の**一次情報ソース**を追加（到達性未確認のため全てdisabled/pending）、
dedup正規化を強化した。安定化Phase（v0.7.0）の機能は全て維持。Private Insightには
一切変更を加えていない（保持をテストと実行で確認）。

### 追加・変更

- **並列取得（§2, §3・`ingestion.py`）**: HTTP取得(fetch_feed)を ThreadPoolExecutor で
  並列化。保存・SQLite index・Cursor更新は**メインスレッドで Source ID 順に直列適用**
  （単一writerを保証しDB/shard競合を防ぐ）。`per_host_max_concurrency` で同一ホストへの
  同時接続数を制限。`max_fetch_workers<=1` で完全逐次（従来動作と同一）。1ソースの例外・
  timeout は隔離。ベンチ: 30ソース×60msで逐次2.13s→並列0.73s（約2.9倍）。
- **config `fetch:` ブロック**: max_fetch_workers(6)/timeout/retry/per_host_max_concurrency。
  併せて `source_limits` / `region_balance` / `category_balance` / `daily_ingestion_targets` を追加
  （監視・選定調整用。保存段階では全件維持）。
- **日本・米国の一次情報 20件追加（`config/sources.yaml`）**: JP10（首相官邸/経産省/金融庁/
  総務省統計局/内閣府/財務省/国交省/日銀統計/JPX/環境省）＋US10（SEC press/EDGAR 8-K・10-Q・
  10-K・6-K/BEA/Census/Federal Register/FTC/FDA）。**全て enabled:false・verify_status:pending**。
  サンドボックスはegress制限で到達性を確認できないため、§6に従い未確認ソースは有効化しない。
  既存disabledのTreasury/BLS/EIAは重複追加せず「再有効化」で対応。
- **Source config schema拡張**: tier / verify_status / secondary_categories / max_items_per_fetch /
  supports_etag / supports_last_modified / rights_classification / rate_limit_policy / notes を新設フィールドとして許容。
- **`src/tank/source_portfolio.py`（新規）**: `validate_sources`（ID/URL重複・必須field・tier/
  source_class/trust妥当性検査）、`coverage_metrics`（Tier1比率・日本比率・region/category多様性・
  集中度）、`coverage_gaps`（不足・偏重の警告）、`classify_disclosure`（企業開示の種別・
  materiality分類。LLM不使用の語彙マッチ）。
- **dedup正規化強化（§12・`url_normalize.py`）**: canonical URLで http→https を畳み、先頭 www を
  除去。scheme差異・www有無で同一記事が別物にならないようにした（異なる記事は統合しない）。
  ※直近の重複率85%は主にRSS再ポーリングの正当なoverlapであり、正規化バグではないと評価。
- **検証モード**: `scripts/run_ingestion.py --verify-candidates`（未有効の候補の到達性のみ確認）。
  workflow_dispatch に `mode`(normal/verify/verify_candidates) 入力を追加（scheduleは常にnormal）。
- **Observability**: run統計・Job Summaryに Coverage（Tier1/日本/米国/地域・カテゴリ多様性・
  top_region/top_category）を追加。

### テスト

- `tests/test_source_portfolio.py`（新規19件）: 並列＝逐次の結果一致・単一writer整合・
  1ソース失敗隔離・決定的順序、Source検証（ID/URL重複・必須field・不正値）、実sources.yamlの
  妥当性、pending全disabled、Coverage指標、開示分類、dedup正規化（http/https/www/slash/
  tracking/fragment畳み込み・別記事非統合）。
- 既存147＋新規11（実質19、既存重複調整後）＝ **158 passed**。
- Private Insight保持は既存 test_stabilization.py で継続検証。

## v0.7.0 (2026-07-21) — Production Stabilization（exit code・日付品質・Source偏重・観測性）

ライブ運用で「Package生成は成功しているのにワークフローが赤(失敗)になる」
「SQLite索引を毎回全再構築する」「2008年など異常日付でシャードが削除される」
といった不安定要因を、最小差分で整えた。新規機能の大規模追加はしていない。
Private Insight（羅針盤）には一切変更を加えていない（本文・AI所感・未来予測・
送信日時が安定化処理の前後で保持されることをテストと実行で確認済み）。

### 修正・追加

- **Exit code / run_status（§2, §4）**: run_ingestion.py の終了コードを是正。
  Package公開が成功していれば一部ソース失敗(403等)・新着0でも **exit 0**。
  致命的障害(全ソース失敗かつ既存Packageなし/検証失敗)のみ **exit 1**。
  exit 2 はCLI引数不正のみ（degradedを2で表さない）。`run_stats.compute_run_status`/
  `resolve_exit_code` を追加し、run_status(healthy/degraded/failed)を統計・ログ・
  Summaryへ出力。全ソース失敗でも既存の有効Packageがあれば degraded / exit 0 で維持。
- **日付品質ガード（§7・新規 `date_quality.py`）**: RSS/Atomのpublished_atが
  未来(>24h)/超過去(>20年)/解析不能なら、記事を**破棄せず** fetched_at へ補正し
  `date_inferred=true` を記録。元の公開日時文字列を `raw_published_at` として保持
  （後から検証可能）。これにより最近の記事が異常年のシャードへ紛れ込み、retentionで
  誤削除されるのを防ぐ。`models.Article` に2フィールド追加、`feed_parser` が
  元日時文字列(`published_raw`)を渡すよう拡張。
- **索引の観測性・retention窓限定（§5）**: SQLite索引の再構築件数・所要秒数を
  ログ＆統計へ記録。再構築対象を retention 窓内のシャードに限定し、増加に伴う
  全再構築の遅延を抑える（索引はシャードから再構築可能な派生データ）。
- **Source偏重（§9・新規 `source_balance.py`）**: 最多ソースの新規占有率・
  concentration_status(ok/warning/critical)・配信Packageのソース別分布を観測して
  Summaryへ表示。**保存段階では全件保持**し、偏重制御は候補・Package選定段階
  （`diversity.select_diverse` の占有率上限、既定20%）でのみ適用。
- **Package保護（§12）**: `publish_package` が検証済みPackageのコピーを
  `published/latest/last_known_good/` へ複製（latestは従来どおり atomic replace で
  壊れない。加えて直前正常版を明示的にバックアップ）。
- **Observability（§15・新規 `scripts/write_summary.py`）**: latest_run.json から
  run_status・取得・保存・品質・配信・Private Insight storage health を Job Summary へ
  整形表示（Private Insightは件数と健全性のみ・本文/タイトルは一切出さない）。
- **config**: `source_balance:` / `date_quality:` ブロックを追加。

### テスト

- `tests/test_stabilization.py`（新規17件）: exit status/run_status マトリクス、
  日付品質(正常/欠損/未来/20年超/元文字列保持/誤解析記事が当日シャードへ)、
  Source偏重観測、既存Package判定、last-known-good、**Private Insight保持
  （retention実行前後で本文・AI所感・未来予測・送信日時が不変）**。
- 130（既存）＋17（新規）＝ **147 passed**。
- 手動2回実行（フェッチ層のみstub・他は本物のコードパス）:
  Run#1 = healthy/exit 0（新規8件・Package公開・LKG生成）、
  Run#2 = degraded/exit 0（304×2＋403×1・新着0・Package維持）、
  Private Insight は両run前後で保持を確認。

## v0.6.0 (2026-07-20) — Rashinban Private Insight Vault（private記事の保管・AI分析）

daily-market-briefから転送された記事本文を非公開領域で分析し、allowlist済みの
派生情報だけを返す機能。本文の正式保存先はCloudflare Worker + KV（非公開）のみで、
このPublicリポジトリには本文・分析生データを一切コミットしない。

### 追加

- `src/tank/private_insight.py`（新規）: `PrivateInsightRecord`/`ForecastRecord`
  dataclass、`LocalPrivateInsightStore`（raw/analysis/index分離・.gitignore領域専用）、
  `intake()`（サーバー側タイムスタンプ・本文ハッシュ重複検知・空/超過本文の拒否）、
  `LocalRuleBasedFallbackAdapter`（テーマ別プレイブック: 電力/半導体/AI/金融政策/
  原油/防衛/為替 → 確認指標・無効化条件・恩恵/逆風セクター）、
  `AnthropicAnalysisAdapter`（ANTHROPIC_API_KEYがある場合のみ）、
  `analyze_record()`（LLM失敗時はルールベースへフォールバック）、
  `build_forecasts()`（base/upside/downside/tail_riskのシナリオ形式・
  確信度上限0.60・invalidation trigger必須・次回検証日つき）、
  `build_derived_summary()`（allowlist方式。本文・長文引用・認証情報・内部エラーは
  構造的に含められない。禁止キー検査つき）、
  `sync_and_analyze_from_worker()`（Worker queue取得→分析→結果送信）。
- `scripts/run_private_insight_analysis.py`（新規）: 分析CLI。
  Secrets未設定なら何もせず正常終了。ログには件数とIDのみ出力（本文は出さない）。
- `.github/workflows/private-insight-analysis.yml`（新規）: 毎時 :11/:41 に分析実行。
  Secrets `INSIGHT_API_URL` / `INSIGHT_API_TOKEN`（必須）、
  `ANTHROPIC_API_KEY` / `PRIVATE_INSIGHT_MODEL`（任意）。
- `config.yaml`: `private_insight:` / `private_insight_analysis:` ブロック
  （確信度上限: article_based_hypothesis 0.60 / historically_supported 0.75 /
  market_confirmed 0.85 / speculative 0.40）。
- `.gitignore`: `data/private_insights/*` を除外（`.gitkeep`のみ許可）。
  本文がgit管理下へ入らないことを構造的に保証。
- `README.md`: 機能説明と有料記事の利用規約注意（本文はユーザー個人のprivate資料
  として扱い、規約適合はユーザー自身が確認する）を追記。
- `tests/test_private_insight.py`（新規12件）: intake・重複検知・タイトル
  フォールバックの本文非漏えい・派生summaryの禁止キー・シナリオ生成・
  確信度上限・ルールベース分析を検証。

### pytest

130 passed（既存118＋新規12）。

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
