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

初期状態で26ソース（多地域・多カテゴリ、AI/半導体に偏らない構成）を同梱しています。
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

## 実装フェーズ（依頼書§29準拠）

- Phase A: Article Store（models/url_normalize/dedup/storage/cursor） ✅
- Phase B: Intelligence Processing（classify/cluster/scoring/diversity/market_reaction/historical） ✅
- Phase C: Publication Layer（publication.py・adapters/） ✅
- Phase D: Market Intelligence Consumer（別リポジトリ daily-market-brief 側で実装） ✅

## 今回実装していないもの

World Intelligence Engine本体・生成AIによる自由作文・ベクトルDB・有料API・
全過去記事のmarket reaction backfill・実際のGitHubリポジトリ自動作成・
実際のRSS/APIフェッチャー実装（`_fetch_source()` はプレースホルダ）。
