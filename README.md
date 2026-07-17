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

`config.yaml` の `sources: []` は初期状態で空です（安全側デフォルト）。
公開RSS・公開API・公式発表のURLのみを追加してください（有料記事・ログイン必須
ページ・利用規約が不明なスクレイピング対象は追加しないこと）。

```yaml
sources:
  - name: "official_source_example"
    url: "https://example.gov/press.rss"
    type: "rss"
    trust: 0.9
    country: "US"
    language: "en"
```

実際のRSS取得処理は `scripts/run_ingestion.py` の `_fetch_source()` へ実装してください
（本リポジトリはネットワークアクセスを行わない安全な既定実装のまま同梱しています）。

## 実行

```bash
python scripts/run_ingestion.py            # 増分取得 + 配信パッケージ生成
python scripts/run_ingestion.py --dry-run  # 取得のみ（配信パッケージは生成しない）
```

`sources` が空のままなら新着0件として高速終了し、既存の `published/latest/` は
上書きされません（安全側）。

## GitHubへ新規リポジトリを作成する手順

1. GitHub上で `takehiro104toshi-cmd/article-intelligence-data-tank` という名前で
   新規リポジトリを作成する（Private/Public はお好みで）。
2. 本フォルダをそのリポジトリのルートとして push する:
   ```bash
   cd article-intelligence-data-tank
   git remote add origin https://github.com/takehiro104toshi-cmd/article-intelligence-data-tank.git
   git push -u origin main
   ```
3. リポジトリの Settings > Secrets and variables > Actions で、実際のニュース取得に
   必要なSecret（あれば）を設定する。
4. Settings > Actions > General で Workflow permissions を
   "Read and write permissions" にする（`article-tank-update.yml` が
   `data/article_store` と `published/` をcommit/pushするため）。
5. `config.yaml` の `sources` へ実際の公開RSS/公式APIのURLを追加する。
6. 手動実行（Actions タブ > Article Tank Update > Run workflow）で動作確認する。

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
