# Article Intelligence Data Tank

Market Intelligence System（daily-market-brief）とは**別リポジトリの独立プロジェクト**。
数千〜数万件のニュース記事を取得・正規化・重複排除・分類・イベント統合・保存し、
**軽量な Published Intelligence Package** だけを生成して配信する。

Market Intelligence System 側は本プロジェクトの内部データ（記事本体・重い検索
インデックス）に一切触れず、`published/latest/` の軽量パッケージだけを取得する。

```
Article Intelligence Data Tank（本リポジトリ）
  ↓ 記事取得・正規化・重複排除・分類・イベント統合・市場影響分析・永続保存
  ↓ 配信候補選定（総合影響度・多様性制御）
  ↓ Published Intelligence Package 生成（軽量・件数上限つき）
Market Intelligence System（別リポジトリ）
  ↓ HTTPで manifest.json / intelligence_package.json.gz を取得するだけ
  1日6回のレポート生成で利用
```

## ディレクトリ構成

```
article-intelligence-data-tank/
  src/tank/            # 収集・正規化・重複排除・分類・クラスタリング・スコアリング・
                       # 多様性制御・市場反応・履歴検索・配信パッケージ生成
    adapters/          # Publication Adapter（配信先の差し替え口）
  scripts/
    run_ingestion.py   # 増分取得 → 配信パッケージ生成のCLIエントリーポイント
  tests/               # pytest（68件）
  config.yaml          # sources / article_tank / publication 設定
  data/article_store/  # 内部保存（記事シャード・索引・cursor・cluster・private本文等）
  published/latest/    # Market Intelligence 向け配信物（manifest.json / *.json.gz）
  .github/workflows/article-tank-update.yml   # 独立ワークフロー（1日6回、Market
                       # Intelligence の生成時刻より15分前に更新）
```

## 保存方式（実装上の技術選択）

依頼書では Parquet + DuckDB が「推奨構成」として例示されていますが、本実装では
**標準ライブラリのみ**（`json` / `sqlite3` / `gzip` / `hashlib`）で `storage_backend:
"local_sharded"` を実装しています。理由:

- 依頼書自身が「必要に応じてSQLite index」を明示的に許容している
- 追加の重い依存（pyarrow/duckdb）を入れずにCIを安定させられる（§25の方針に合致）
- `src/tank/storage.py`（シャード読み書き）と `src/tank/index.py`（横断検索）は
  インターフェースを保てば Parquet/DuckDB へ差し替え可能な設計にしてある
  （呼び出し側のコードは変更不要）

将来 Parquet/DuckDB へ切り替える場合は `requirements.txt` のコメントに従い
`pyarrow`/`duckdb` を追加インストールし、`ArticleStore`/`ArticleIndex` と同じ
メソッドシグネチャを持つ実装へ差し替えてください。

## セットアップ

```bash
cd article-intelligence-data-tank
pip install -r requirements.txt
pytest -q
```

## ニュース取得先の追加

ニュースソースは **`config/sources.yaml`** で管理します（`config.yaml` の `sources:` へ
直接書くこともでき、両者はマージされます）。公開RSS/Atomのみ。有料記事・ログイン必須
ページ・利用規約が不明なスクレイピング対象は追加しないでください。全文取得は行わず、
フィードの要約（summary）だけを取り込みます。

```yaml
# config/sources.yaml
sources:
  - id: fed_press
    name: U.S. Federal Reserve — Press Releases
    url: "https://www.federalreserve.gov/feeds/press_all.xml"
    enabled: true
    format: auto            # auto / rss / atom
    source_class: primary_official
    country: US
    region: North America
    language: en
    primary_category: monetary_policy
    trust_score: 98         # 0-100
    fetch_interval_minutes: 60
```

初期状態で48ソース（多地域・多カテゴリ、AI/半導体に偏らない構成）を同梱しています。
ただし各URLの到達性はビルド環境で確認できていないため、**初回のライブ取得の前に
必ず到達性を確認**してください（不確実なものは `enabled: false` にしてあります）。

## 実行

```bash
# 0) 各enabledソースの到達性だけ確認する（保存しない）。FAIL は enabled: false にする
python scripts/run_ingestion.py --verify

# 1) 実際に取得→分類→クラスタ→保存→配信Package生成
python scripts/run_ingestion.py

# 取得・保存まで（配信Packageは作らない）
python scripts/run_ingestion.py --dry-run
```

動作の要点:

- **増分取得**: 各ソースの ETag / Last-Modified を Cursor に保存し、次回は条件付きGET。
  変更が無ければ HTTP 304 で高速終了（記事は保存しない）。
- **重複排除**: 同じ記事は再保存しない（2回目以降は new=0 / duplicates>0）。
- **障害の分離**: 1ソースが失敗（403/404/429/500/timeout）しても他は継続。
  403/404/429 は再試行しない。全ソース失敗時は既存の `published/latest/` を
  空Packageで上書きしません（保護）。
- **終了コード**: 0=成功 / 2=degraded（一部失敗だが公開）/ 1=失敗（全失敗で非公開・生成失敗）。
- **統計**: `data/article_store/statistics/latest_run.json` に各回の統計を保存
  （Secret/Tokenは出力しません）。

配信物は `published/latest/manifest.json` と `intelligence_package.json.gz` に生成されます。

## GitHubへ新規リポジトリを作成する手順

1. GitHub上で `takehiro104toshi-cmd/article-intelligence-data-tank` という名前で
   新規リポジトリを作成する。**Public を推奨**（下記の raw URL 配信は Public 前提。
   取り込むのは公開RSSの見出し・要約・分類などの公開情報のみ。private本文は
   .gitignore で除外され push されません）。
2. 本フォルダをそのリポジトリのルートとして push する（ブランチは main）:
   ```bash
   cd article-intelligence-data-tank
   git remote add origin https://github.com/takehiro104toshi-cmd/article-intelligence-data-tank.git
   git branch -M main
   git push -u origin main
   ```
3. Settings > Actions > General > Workflow permissions を
   **"Read and write permissions"** にする（`article-tank-update.yml` が
   取得結果（シャード/cursor/配信Package）を commit/push するため）。
4. 手動実行（Actions タブ > **Article Tank Update** > Run workflow）で動作確認する。
   実行後、リポジトリに `published/latest/manifest.json` と
   `intelligence_package.json.gz` がコミットされていれば成功。

   ※ 永続化の設計: SQLite索引（バイナリ）は .gitignore で追跡せず、
   毎回コミット済みシャード（テキスト）から自動再構築します。これにより
   増分取得・重複排除を保ちつつ repo の肥大化を防ぎます。cursor / shards /
   配信Package はコミットされます。

## Market Intelligence System からの接続手順

Market Intelligence System 側の `config.yaml` に以下を設定してください
（詳細は daily-market-brief 側の README / CHANGELOG を参照）。

```yaml
external_intelligence:
  enabled: true
  manifest_url: "https://raw.githubusercontent.com/takehiro104toshi-cmd/article-intelligence-data-tank/main/published/latest/manifest.json"
  package_url: "https://raw.githubusercontent.com/takehiro104toshi-cmd/article-intelligence-data-tank/main/published/latest/intelligence_package.json.gz"
```

（GitHub Pages を有効にした場合は Pages の公開URLに置き換えてください。)

## Rashinban Private Insight Vault（private記事の保管・分析 / v0.6）

daily-market-brief のレポート画面から転送された記事本文を、**非公開領域**で
AI分析（要約・所感・因果・市場影響・シナリオ形式の未来予測・検証条件）する機能。

- 本文の正式な保存先は **Cloudflare Worker + KV（非公開）のみ**。このリポジトリは
  Public のため、本文・分析生データは **絶対に git 管理下へ置かない**。
  `data/private_insights/` は `.gitignore` で除外済み（Actionsランナー上の
  一時作業領域としてのみ使用）。
- 公開されるのは `build_derived_summary()` が生成する **allowlist済みの派生情報のみ**
  （テーマ・シナリオ・確認指標・検証日など）。本文・長文引用・認証情報・内部エラーは
  構造的に含められない。
- 分析は `.github/workflows/private-insight-analysis.yml`（毎時 :11/:41）が
  Worker の queue を取得して実行。Secrets `INSIGHT_API_URL` / `INSIGHT_API_TOKEN`
  が未設定なら**何もせず正常終了**する（既存機能に影響なし）。
  `ANTHROPIC_API_KEY` があればLLM分析、無ければルールベース分析で動く。
- 未来予測は断定ではなく **シナリオ形式**（base/upside/downside/tail_risk）で、
  確信度に上限（記事1本のみ由来: 0.60）と **無効化条件（invalidation trigger）** を必ず付ける。

> **有料記事の取り扱いについての注意**: 転送される本文（例: 日経電子版の記事）は、
> ユーザー本人が正当に購読・取得した**個人的なprivate資料**として非公開保存する
> 前提です。各媒体の利用規約上どこまで許されるか（私的複製の範囲等）は媒体ごとに
> 異なるため、**ご自身で利用規約を確認**してください。本システムは本文を公開領域へ
> 出さない設計ですが、規約適合性を保証するものではありません。

## 運用の安定化（Production Stabilization / v0.7）

継続運用で壊れにくくするための整備。詳細は CHANGELOG を参照。

- **終了コードの意味**: `0` = healthy または degraded（有効なPackageがあり利用可能）。
  一部ソースが403/429/timeout・新着0でも Package が公開できていれば 0（ワークフローは
  緑のまま）。`1` = failed（全ソース失敗かつ既存Packageなし／Package検証失敗などの致命
  障害のみ）。`2` = CLI引数不正のみ。GitHub Actions の Job Summary に
  `HEALTHY / DEGRADED / FAILED` と主要指標が表示される。
- **日付品質**: RSS/Atomの published_at が未来/20年超過去/解析不能な場合、記事は破棄せず
  `fetched_at` へ補正し `date_inferred=true`・元文字列 `raw_published_at` を保持する。
  異常日付の記事が古いシャードへ紛れて retention に誤削除されるのを防ぐ。
- **索引**: SQLite索引はシャード(=source of truth)から再構築可能な派生データ。GitHub
  Actionsのcheckoutには含まれない（.gitignore）ため空なら再構築するが、retention窓内に
  限定し、再構築の件数・秒数をログ表示する。索引が消えても記事は失われない。
- **Source偏重**: 保存は全件保持。配信Package・候補選定の段階でのみ、同一ソースの占有率に
  上限（`source_balance.max_published_share`）を課す。集中度は Summary に表示。
- **Package保護**: `published/latest/` は atomic replace で壊れない。加えて直前の正常版を
  `published/latest/last_known_good/` へ複製する。
- **復旧**: 索引を作り直したい場合は `python scripts/run_ingestion.py`（空なら自動再構築）。
  到達性だけ確認したい場合は `--verify`。

> **Private Insight（羅針盤）はこの安定化の対象外**です。取得・保存・配信・retention の
> どの処理も `data/private_insights/` を読み書きしません。本文・AI所感・未来予測・送信日時は
> 安定化処理の前後で保持されます（`tests/test_stabilization.py` で明示的に検証）。

## Source Portfolio 拡張（Phase 3 Batch 1 / v0.8）

150〜250ソースへ段階拡張するための基盤整備と、日米一次情報の追加（第1弾）。

- **並列取得**: HTTP取得のみ `fetch:` の `max_fetch_workers`（既定6）で並列化。保存・索引・
  Cursor更新は単一writerで直列適用（SQLite/shard競合を防ぐ）。`per_host_max_concurrency` で
  同一ホストへの同時接続を制限。`max_fetch_workers: 1` にすると完全逐次に戻せる。
- **候補ソースの到達性確認（重要）**: Batch 1で追加した日米一次情報は、環境の都合で到達性を
  未確認のため **すべて `enabled: false` / `verify_status: pending`** です。本番導入前に必ず:

  ```bash
  python scripts/run_ingestion.py --verify-candidates   # 未有効の候補だけ到達性チェック
  ```

  （GitHub Actions では workflow_dispatch の `mode = verify_candidates` でも実行可）
  OK / 304 になったソースだけ `config/sources.yaml` で `enabled: true` にしてください。
  確認できないURLは有効化しないでください（§6）。
- **Coverage監視**: run統計・Job Summaryに Tier1比率・日本比率・地域/カテゴリ多様性・集中度を表示。
- **dedup**: canonical URLで http↔https・www有無を吸収（同一記事の重複すり抜けを防止）。
- **企業開示分類**: `source_portfolio.classify_disclosure()` が決算/予想修正/自社株買い/M&A/
  大型受注/訴訟等を語彙マッチで分類（LLM不使用）。
- **SEC EDGAR用の連絡先付きUser-Agent**: SEC EDGAR等は「連絡先メールを含むUser-Agent」を
  要求し、無いと403になる。GitHubリポジトリの Secrets に `DATA_TANK_CONTACT_EMAIL`（自分の
  連絡先メール）を登録すると、取得時のUAにそのメールが付与され、`mode=verify_candidates` で
  EDGAR/METI等を再確認できる。未設定でも他ソースの取得は継続する（EDGARだけ403になり得る）。
  ブラウザ偽装・UAなりすまし・アクセス制限回避は一切行わない。

> Private Insight（羅針盤）はこの拡張の対象外です。Source取得・保存・配信の処理は
> `data/private_insights/` を一切読み書きしません。

## API Source Adapter Layer: EDINET / e-Stat（Batch 1.5 / v0.9）

RSS/Atom専用だった取得層を拡張し、公式JSON APIソースを既存パイプラインへ供給する共通
アダプタ（`src/tank/source_adapters.py`）を追加しました。第一弾は **EDINET（金融庁 法定開示
API v2）** と **e-Stat（政府統計 API v3.0）** です。取得後の正規化・dedup・保存・Package生成は
すべて従来のまま流れます（本文＝PDF/XBRLは取得しません。開示/統計の**メタデータのみ**）。

### 責務の境界（誤解しやすい点）

- **EDINET は TDnet／適時開示の完全な代替ではありません。** EDINETの役割は法定開示・定期報告
  （有価証券報告書/四半期/半期）・臨時報告書・大量保有報告です。決算短信・業績予想の適時開示
  など EDINET で取得できないものは「未取得」として扱い、推測で埋めません。
- **e-Stat は記事数を増やすソースではなく「マクロ統計ソース」です。** GDP/CPI/雇用/消費/生産/
  貿易/家計/企業/人口/住宅建設 など影響の大きい系列（`api_sources.estat.series_whitelist`）に
  該当する統計表の更新だけを対象化します。

### 使い方（APIキーの登録が必要）

1. **EDINET**: EDINET APIの利用登録で無料の Subscription-Key を取得。
   **e-Stat**: e-Stat（政府統計の総合窓口）でユーザー登録し無料の appId を取得。
2. GitHubリポジトリの Secrets に登録:
   - `EDINET_SUBSCRIPTION_KEY` = EDINETのSubscription-Key
   - `ESTAT_APP_ID` = e-StatのappId
   （未設定でも他ソースの取得・Package生成は継続します＝該当ソースだけ DEGRADED/exit0 でスキップ）
3. **実レスポンスで対応表を検証**してから有効化してください。特に EDINET の `docTypeCode` の
   実値と `config.yaml` の `api_sources.edinet.doctype_map` が一致するかを確認します（暫定値は
   公式ドキュメント準拠の想定です）。
4. 問題なければ `config/sources.yaml` の `edinet_disclosures` / `estat_macro` を
   `enabled: true` / `verify_status: verified_healthy` に変更します。

> API系ソースはRSSの到達性チェック（`--verify-candidates`）では検証できません。キー登録前は
> `verify_status: pending_api_key` / `enabled: false` のまま安全に保持されます。
>
> ⚠️ 現状、**実APIレスポンスでの検証は未実施**です（開発環境が外部ネットワーク不可のため）。
> JSONキー名は公式ドキュメント準拠で防御的に実装していますが、実運用前に必ずご自身の環境で
> 実レスポンスを確認してください。HTMLスクレイピングは行わず、公式JSON APIのみを使用します。

## 実装フェーズ（依頼書§29準拠）

- Phase A: Article Store（models/url_normalize/dedup/storage/cursor） ✅
- Phase B: Intelligence Processing（classify/cluster/scoring/diversity/market_reaction/historical） ✅
- Phase C: Publication Layer（publication.py・adapters/） ✅
- Phase D: Market Intelligence Consumer（別リポジトリ daily-market-brief 側で実装） ✅

## 今回実装していないもの

World Intelligence Engine本体・生成AIによる自由作文・ベクトルDB・有料API・
全過去記事のmarket reaction backfill・実際のGitHubリポジトリ自動作成・
実際のRSS/APIフェッチャー実装（`_fetch_source()` はプレースホルダ）。
