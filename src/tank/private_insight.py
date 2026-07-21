"""Rashinban Private Insight Vault — private記事の保存・分析・未来予測（Data Tank側）。

ユーザーが個人的に取得・貼り付けた記事本文（日経新聞等のprivate資料）を、
公開領域へ一切出さずに保存・分析するモジュール。

★アーキテクチャ上の最重要制約:
  本リポジトリ（article-intelligence-data-tank）は Public であるため、
  「gitへコミットされたものはすべて公開」になる。したがって:
    - 本番のprivate本文の永続保存先は Cloudflare Worker + KV（非公開）である。
      本モジュールの LocalPrivateInsightStore はローカル開発・テスト・
      GitHub Actions 実行中の一時領域（.gitignore 済み・コミット禁止）専用。
    - GitHub Actions 上では本文はメモリ/一時ディレクトリのみを通過し、
      リポジトリへは絶対にコミットしない。ログにも本文を出力しない。
    - 公開 Published Package / 公開HTML へは allowlist された派生情報
      （build_derived_summary）だけを渡す。raw本文はallowlistに存在しないため
      構造的に混入しない。

分析は決定論的なルールベース（LocalRuleBasedFallbackAdapter）を既定とし、
ANTHROPIC_API_KEY / OPENAI_API_KEY が設定されている場合のみLLM Adapterを使う。
LLM停止時・キー未設定時もルールベースへフォールバックし、記事は失われない。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .classify import classify_article
from .models import Article

_JST = timezone(timedelta(hours=9))

DEFAULT_MAX_BODY_CHARS = 30000

# 記事1本のみの仮説に許す confidence 上限（§9。configで上書き可能）
CONFIDENCE_CAPS = {
    "article_based_hypothesis": 0.60,
    "market_confirmed": 0.85,
    "historically_supported": 0.75,
    "speculative": 0.40,
}


# ---------- モデル（§6, §10） ----------

@dataclass
class PrivateInsightRecord:
    private_article_id: str = ""
    request_id: str = ""
    source_name: str = ""
    source_url: str = ""
    title: str = ""
    intake_method: str = "manual_paste"
    rights_classification: str = "user_private_paid_article"
    visibility: str = "private"          # 常にprivate

    raw_body_storage_key: str = ""
    body_hash: str = ""
    character_count: int = 0
    body_available: bool = False
    raw_body_encrypted: bool = False

    submitted_at_utc: str = ""
    submitted_at_jst: str = ""
    submitted_history: List[str] = field(default_factory=list)
    article_published_at: str = ""
    analyzed_at: str = ""
    last_updated_at: str = ""

    user_note: str = ""
    reason_for_interest: str = ""
    user_tags: List[str] = field(default_factory=list)

    status: str = "received"             # received/stored/queued/analyzing/completed/failed/needs_review/duplicate
    analysis_version: str = ""
    model_provider: str = ""
    model_name: str = ""
    retry_count: int = 0
    error_code: str = ""

    related_private_article_ids: List[str] = field(default_factory=list)

    deleted_at: str = ""
    delete_type: str = ""                # "" / soft / permanent
    deleted_by: str = ""
    delete_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PrivateInsightRecord":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ForecastRecord:
    forecast_id: str = ""
    private_article_id: str = ""
    created_at: str = ""
    horizon: str = ""                    # 1w/1m/3m/1y/3-5y
    scenario_type: str = ""              # base/upside/downside/tail_risk
    scenario_title: str = ""
    probability_range: str = ""
    prediction: str = ""
    expected_developments: List[str] = field(default_factory=list)
    affected_markets: List[str] = field(default_factory=list)
    affected_sectors: List[str] = field(default_factory=list)
    leading_indicators: List[str] = field(default_factory=list)
    confirmation_triggers: List[str] = field(default_factory=list)
    invalidation_triggers: List[str] = field(default_factory=list)
    next_review_date: str = ""
    confidence: float = 0.0
    evidence_level: str = "article_based_hypothesis"
    validation_status: str = "pending"   # pending/partially_confirmed/confirmed/invalidated/expired/unknown
    observed_outcome: str = ""
    result_score: Optional[float] = None
    analyst_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- 保存（ローカル開発/テスト用Adapter。本番はWorker+KV） ----------

def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pi-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def normalize_body_hash(body: str) -> str:
    normalized = re.sub(r"[\s　]+", "", (body or "").strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class LocalPrivateInsightStore:
    """raw本文と分析結果を分離して保存するローカルAdapter（.gitignore済み領域専用）。

    layout:
      <base>/raw/YYYY/MM/<id>.txt        … 本文（非公開・git対象外）
      <base>/analysis/YYYY/MM/<id>.json  … 分析＋予測（非公開・git対象外）
      <base>/index.json                  … メタデータ索引（本文は含まない）
    """

    def __init__(self, base_dir: str):
        self.base = Path(base_dir)
        self.raw_dir = self.base / "raw"
        self.analysis_dir = self.base / "analysis"
        self.index_path = self.base / "index.json"
        self.base.mkdir(parents=True, exist_ok=True)

    # -- index --
    def _load_index(self) -> Dict[str, dict]:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_index(self, index: Dict[str, dict]) -> None:
        _atomic_write(self.index_path, json.dumps(index, ensure_ascii=False, indent=2))

    def _month_key(self, record: PrivateInsightRecord) -> str:
        ts = record.submitted_at_utc or datetime.now(timezone.utc).isoformat()
        return f"{ts[0:4]}/{ts[5:7]}"

    # -- intake --
    def find_duplicate(self, body_hash: str) -> Optional[str]:
        for pid, meta in self._load_index().items():
            if meta.get("body_hash") == body_hash and meta.get("delete_type") != "permanent":
                return pid
        return None

    def save_new(self, record: PrivateInsightRecord, body: str) -> PrivateInsightRecord:
        """新規記事を保存する（重複判定は呼び出し側 intake() が行う）。"""
        month = self._month_key(record)
        raw_path = self.raw_dir / month / f"{record.private_article_id}.txt"
        _atomic_write(raw_path, body)
        record.raw_body_storage_key = str(raw_path.relative_to(self.base))
        record.body_available = True
        record.status = "stored"
        self._upsert_meta(record)
        return record

    def _upsert_meta(self, record: PrivateInsightRecord) -> None:
        index = self._load_index()
        meta = record.to_dict()
        # index には本文由来の長文フィールドを持たせない（保存キーとハッシュのみ）
        index[record.private_article_id] = meta
        self._save_index(index)

    def get(self, private_article_id: str) -> Optional[PrivateInsightRecord]:
        meta = self._load_index().get(private_article_id)
        return PrivateInsightRecord.from_dict(meta) if meta else None

    def read_body(self, record: PrivateInsightRecord) -> str:
        if not record.raw_body_storage_key:
            return ""
        path = self.base / record.raw_body_storage_key
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def save_analysis(self, private_article_id: str, analysis: dict) -> None:
        record = self.get(private_article_id)
        if record is None:
            return
        month = self._month_key(record)
        path = self.analysis_dir / month / f"{private_article_id}.json"
        _atomic_write(path, json.dumps(analysis, ensure_ascii=False, indent=2))
        record.status = "completed"
        record.analyzed_at = datetime.now(timezone.utc).isoformat()
        record.last_updated_at = record.analyzed_at
        record.analysis_version = analysis.get("analysis_version", "v1")
        record.model_provider = analysis.get("model_provider", "")
        record.model_name = analysis.get("model_name", "")
        self._upsert_meta(record)

    def read_analysis(self, private_article_id: str) -> Optional[dict]:
        record = self.get(private_article_id)
        if record is None:
            return None
        path = self.analysis_dir / self._month_key(record) / f"{private_article_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def list_records(self, include_deleted: bool = False) -> List[PrivateInsightRecord]:
        out = []
        for meta in self._load_index().values():
            rec = PrivateInsightRecord.from_dict(meta)
            if not include_deleted and rec.delete_type:
                continue
            out.append(rec)
        out.sort(key=lambda r: r.submitted_at_utc, reverse=True)
        return out

    # -- delete（§18） --
    def delete(self, private_article_id: str, permanent: bool = False,
               deleted_by: str = "user", reason: str = "") -> bool:
        record = self.get(private_article_id)
        if record is None:
            return False
        now = datetime.now(timezone.utc).isoformat()
        record.deleted_at = now
        record.delete_type = "permanent" if permanent else "soft"
        record.deleted_by = deleted_by
        record.delete_reason = reason
        if permanent:
            for rel in (record.raw_body_storage_key,):
                if rel:
                    try:
                        (self.base / rel).unlink(missing_ok=True)
                    except OSError:
                        pass
            apath = self.analysis_dir / self._month_key(record) / f"{private_article_id}.json"
            try:
                apath.unlink(missing_ok=True)
            except OSError:
                pass
            record.raw_body_storage_key = ""
            record.body_available = False
        self._upsert_meta(record)
        return True


# ---------- Intake（保存＋重複判定＋サーバー側timestamp） ----------

def intake(
    store: LocalPrivateInsightStore,
    body: str,
    title: str = "",
    source_name: str = "",
    source_url: str = "",
    article_published_at: str = "",
    user_note: str = "",
    reason_for_interest: str = "",
    user_tags: Optional[List[str]] = None,
    rights_classification: str = "user_private_paid_article",
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
    now: Optional[datetime] = None,
) -> PrivateInsightRecord:
    """本文を受け取り、検証→重複判定→保存する。送信日時はサーバー側で記録する。"""
    body = (body or "").strip()
    if not body:
        raise ValueError("empty_body")
    if len(body) > max_body_chars:
        raise ValueError("body_too_long")

    ts = _now(now)
    body_hash = normalize_body_hash(body)

    dup_id = store.find_duplicate(body_hash)
    if dup_id:
        existing = store.get(dup_id)
        existing.submitted_history.append(ts.isoformat())
        if user_note:
            existing.user_note = (existing.user_note + "\n" + user_note).strip()
        existing.status = existing.status or "duplicate"
        existing.last_updated_at = ts.isoformat()
        store._upsert_meta(existing)
        result = PrivateInsightRecord.from_dict(existing.to_dict())
        result.status = "duplicate"
        return result

    record = PrivateInsightRecord(
        private_article_id="pai_" + body_hash[:20],
        request_id="req_" + uuid.uuid4().hex[:12],
        source_name=source_name,
        source_url=source_url,
        # タイトル未指定時に本文冒頭を流用すると、派生情報（公開レポート）へ本文の
        # 一部が漏れるため、中立的なプレースホルダーを使う（本文は一切引用しない）。
        title=title or f"無題のprivate記事（{ts.astimezone(_JST):%m/%d %H:%M}保存）",
        rights_classification=rights_classification,
        body_hash=body_hash,
        character_count=len(body),
        submitted_at_utc=ts.isoformat(),
        submitted_at_jst=ts.astimezone(_JST).isoformat(),
        submitted_history=[ts.isoformat()],
        article_published_at=article_published_at,
        user_note=user_note,
        reason_for_interest=reason_for_interest,
        user_tags=list(user_tags or []),
        last_updated_at=ts.isoformat(),
    )
    return store.save_new(record, body)


# ---------- 分析Adapter（§8, §15, §16） ----------

_URGENT_HINTS = ("急落", "急騰", "緊急", "破綻", "制裁", "利上げ", "利下げ", "急拡大", "急増")

# テーマ → (確認指標, 無効化トリガー, 恩恵/逆風) の機械的対応表（生成AIなし）
_THEME_PLAYBOOK: Dict[str, dict] = {
    "electric_power": {
        "indicators": ["電力会社の設備投資計画", "変圧器受注", "電線価格", "データセンター建設計画"],
        "invalidation": ["データセンター投資計画の縮小・延期の公表", "電力設備投資額の前年割れ"],
        "beneficiary": ["電力・インフラ", "電機・電線・素材"], "headwind": [],
    },
    "semiconductor": {
        "indicators": ["SOX指数", "半導体製造装置受注", "主要ファウンドリの設備投資計画"],
        "invalidation": ["主要メーカーの設備投資下方修正", "在庫調整の長期化"],
        "beneficiary": ["半導体・電子部品"], "headwind": [],
    },
    "ai": {
        "indicators": ["大手クラウドの設備投資額", "AI関連受注・決算コメント", "SOX指数"],
        "invalidation": ["AI投資計画の縮小公表", "AI関連収益化の失望決算の連続"],
        "beneficiary": ["情報通信・生成AI", "半導体・電子部品"], "headwind": [],
    },
    "monetary_policy": {
        "indicators": ["政策金利", "中央銀行の声明文・議事要旨", "国債利回り"],
        "invalidation": ["想定と逆方向の政策決定", "金利の逆方向への持続的な動き"],
        "beneficiary": ["金融"], "headwind": ["情報通信・生成AI"],
    },
    "oil": {
        "indicators": ["WTI原油価格", "OPEC減産・増産決定", "原油在庫統計"],
        "invalidation": ["原油価格の持続的な逆方向トレンド"],
        "beneficiary": ["資源・エネルギー"], "headwind": ["自動車"],
    },
    "defense": {
        "indicators": ["防衛予算の審議・成立", "防衛関連の受注公表"],
        "invalidation": ["防衛予算の削減・計画撤回"],
        "beneficiary": ["重工業・防衛"], "headwind": [],
    },
    "fx": {
        "indicators": ["ドル円レート", "日米金利差", "為替介入の有無"],
        "invalidation": ["為替の持続的な逆方向トレンド"],
        "beneficiary": ["自動車"], "headwind": [],
    },
}

_DEFAULT_PLAYBOOK = {
    "indicators": ["関連企業の決算・設備投資計画", "関連する政策・規制の動向", "関連市場指数"],
    "invalidation": ["記事が示唆する動きと逆方向の公式発表・統計"],
    "beneficiary": [], "headwind": [],
}

_HORIZONS = ["1w", "1m", "3m", "1y", "3-5y"]


def _classify_body(title: str, body: str) -> Article:
    art = Article(title_original=title or body[:80], description=body[:2000])
    classify_article(art)
    return art


class LocalRuleBasedFallbackAdapter:
    """決定論的なルールベース分析（LLM不要・オフラインテスト可能）。

    生成する所感・予測はテンプレート＋分類結果の機械的な組み合わせであり、
    「機械的所感」であることを明示する。事実（記事分類）・所感・予測を分離する。
    """

    provider = "rule_based"
    model = "local-fallback-v1"

    def analyze(self, record: PrivateInsightRecord, body: str, config: Optional[dict] = None) -> dict:
        config = config or {}
        art = _classify_body(record.title, body)
        themes = [t for t in (art.themes or []) if t != "uncategorized"]
        primary = art.primary_category if art.primary_category != "uncategorized" else (themes[0] if themes else "")
        playbook = _THEME_PLAYBOOK.get(primary, _DEFAULT_PLAYBOOK)
        urgent = any(k in (record.title + body[:500]) for k in _URGENT_HINTS)

        # 本文の要約: 公開派生情報へは本文を引用しないため、内部用にも
        # 「冒頭センテンスの短い抜粋」ではなく分類ベースの機械的要約文を生成する。
        theme_label = primary or "特定テーマなし"
        summary_200 = (
            f"「{record.title}」に関するprivate保存記事。主テーマは{theme_label}。"
            f"本文{record.character_count}字。"
            + ("緊急性の高い語を含む。" if urgent else "")
        )[:200]

        caps = {**CONFIDENCE_CAPS, **(config.get("confidence_caps") or {})}
        forecasts = build_forecasts(record, primary, playbook, caps=caps)

        return {
            "analysis_version": "v1",
            "model_provider": self.provider,
            "model_name": self.model,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "basic": {
                "summary_200": summary_200,
                "summary_3lines": [
                    f"主テーマ: {theme_label}",
                    f"関連テーマ: {', '.join(themes[:5]) or 'なし'}",
                    "詳細な事実関係は原文（private保存）を参照。",
                ],
                "key_points": [f"分類上の主テーマは{theme_label}"],
                "claims": [],
                "facts": ["本記事はユーザーがprivate保存した本文であり、本分析は本文の分類結果のみに基づく"],
                "interpretations": [],
                "speculations": [],
                "unknowns": ["記事本文だけでは市場の実際の反応は確認できない"],
                "source_limitations": ["単一記事のみ。独立ソースによる裏付けは未確認"],
            },
            "classification": {
                "primary_category": art.primary_category,
                "secondary_categories": art.secondary_categories,
                "themes": themes,
                "countries": art.countries,
                "companies": art.companies,
                "event_type": art.event_type,
            },
            "market_intelligence": {
                "short_term_impact": "記事単体では市場反応は未確認（article_based_hypothesis）",
                "mid_term_impact": f"{theme_label}関連の投資・受注動向に波及する可能性",
                "long_term_impact": "構造的テーマに該当する場合は中長期の資金配分に影響し得る",
                "beneficiary_sectors": playbook["beneficiary"],
                "headwind_sectors": playbook["headwind"],
                "causal_chain": [theme_label, "関連業種の受注・投資", "関連銘柄の業績期待"],
                "second_order_effects": ["サプライチェーン周辺業種への波及"],
                "counter_scenario": "記事が示唆する動きが実現せず、関連テーマの物色が続かない可能性",
            },
            "ai_analyst_impression": {
                "label": "AI Analyst Impression（機械的所感・事実とは分離）",
                "why_important": f"主テーマ{theme_label}は保存時点でユーザーが注目した領域であり、テーマ蓄積の材料になる",
                "overlooked_points": ["一次波及だけでなく周辺サプライチェーンへの二次波及"],
                "market_not_yet_pricing": "公開ニュース・市場データでの確認が取れるまでは仮説段階",
                "cannot_judge_from_article_alone": ["実際の市場反応", "他社・業界全体への広がり"],
                "combine_with": ["Data Tankの公開ニュース", "market_reactions", "テーマ別momentum"],
                "sales_hint": f"「{theme_label}」関連の顧客会話の切り口として利用可能",
                "strategist_view": "単発記事ではなくテーマの持続性で評価する",
                "relation_to_past_saves": [],
            },
            "forecasts": [f.to_dict() for f in forecasts],
        }


def build_forecasts(record: PrivateInsightRecord, primary_theme: str, playbook: dict,
                    caps: Optional[dict] = None, now: Optional[datetime] = None) -> List[ForecastRecord]:
    """base/upside/downside/tail_risk の4シナリオ×代表horizonを生成する（§9）。

    記事1本のみの仮説であるため evidence_level=article_based_hypothesis とし、
    confidence は caps["article_based_hypothesis"]（既定0.60）を超えない。
    """
    caps = caps or CONFIDENCE_CAPS
    cap = float(caps.get("article_based_hypothesis", 0.60))
    ts = _now(now)
    theme_label = primary_theme or "記事テーマ"
    next_review = (ts + timedelta(days=30)).date().isoformat()

    scenarios = [
        ("base", f"{theme_label}関連の動きが緩やかに継続", "40-60%", min(cap, 0.55), "1m"),
        ("upside", f"{theme_label}関連の投資・需要が加速", "15-30%", min(cap, 0.35), "3m"),
        ("downside", f"{theme_label}関連の期待が剥落・調整", "15-30%", min(cap, 0.35), "3m"),
        ("tail_risk", f"{theme_label}を取り巻く前提が急変（規制・地政学等）", "1-10%", min(cap, 0.15), "1y"),
    ]
    out: List[ForecastRecord] = []
    for stype, title, prob, conf, horizon in scenarios:
        out.append(ForecastRecord(
            forecast_id="fc_" + uuid.uuid4().hex[:12],
            private_article_id=record.private_article_id,
            created_at=ts.isoformat(),
            horizon=horizon,
            scenario_type=stype,
            scenario_title=title,
            probability_range=prob,
            prediction=f"{title}する可能性（記事ベースの仮説であり断定ではない）",
            expected_developments=[f"{theme_label}関連の発表・受注・投資計画の増減"],
            affected_markets=["日本株", "関連コモディティ/為替"],
            affected_sectors=list(playbook.get("beneficiary", [])) + list(playbook.get("headwind", [])),
            leading_indicators=list(playbook.get("indicators", [])),
            confirmation_triggers=[f"{ind}の記事方向への変化" for ind in playbook.get("indicators", [])[:2]],
            invalidation_triggers=list(playbook.get("invalidation", [])),
            next_review_date=next_review,
            confidence=round(conf, 2),
            evidence_level="article_based_hypothesis",
        ))
    return out


class AnthropicAnalysisAdapter:
    """Anthropic API を使う分析Adapter。キー未設定・失敗時は呼び出し側でfallback。

    store_raw_prompt/store_raw_response は常にFalse相当（本文・プロンプトを保存しない）。
    """

    provider = "anthropic"

    def __init__(self, model: str = "", timeout: int = 60):
        self.model = model or os.environ.get("PRIVATE_INSIGHT_MODEL", "claude-sonnet-5")
        self.timeout = timeout

    def available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def analyze(self, record: PrivateInsightRecord, body: str, config: Optional[dict] = None) -> dict:
        import requests

        prompt = _build_llm_prompt(record, body)
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        m = re.search(r"\{.*\}", text, re.DOTALL)
        parsed = json.loads(m.group(0) if m else text)
        parsed.setdefault("analysis_version", "v1")
        parsed["model_provider"] = self.provider
        parsed["model_name"] = self.model
        parsed["analyzed_at"] = datetime.now(timezone.utc).isoformat()
        _enforce_confidence_caps(parsed, (config or {}).get("confidence_caps"))
        return parsed


def _build_llm_prompt(record: PrivateInsightRecord, body: str) -> str:
    """§16 の品質要件を明示した内部プロンプト。本文の長文引用・再掲を禁止する。"""
    return (
        "あなたは証券会社の中立的なリサーチアシスタントです。以下のprivate保存記事を分析し、"
        "指定のJSONだけを出力してください。\n"
        "厳守事項:\n"
        "- 入力記事の内容だけを事実として扱い、外部事実を勝手に補完しない\n"
        "- 事実・解釈・推測・予測を明確に分離する（推測には『推測』と明記）\n"
        "- 記事にない企業名を乱発しない\n- 投資助言と断定しない\n"
        "- 未来予測はbase/upside/downside/tail_riskのシナリオ形式で、confidence(0-1)と"
        "evidence_level(article_based_hypothesis固定)、confirmation_triggers、"
        "invalidation_triggers、next_review_dateを必ず含める\n"
        "- confidenceは0.60を超えない（記事1本のみのため）\n"
        "- 本文を長く引用しない（要約はすべて自分の言葉で200字以内）\n"
        "- 出力はJSONのみ。キー: basic{summary_200,summary_3lines,key_points,facts,"
        "interpretations,speculations,unknowns,source_limitations}, classification{...}, "
        "market_intelligence{...}, ai_analyst_impression{...}, forecasts[...]\n\n"
        f"タイトル: {record.title}\n出典: {record.source_name}\n"
        f"記事日時: {record.article_published_at}\nユーザーメモ: {record.user_note}\n"
        f"本文:\n{body}"
    )


def _enforce_confidence_caps(analysis: dict, caps: Optional[dict] = None) -> None:
    merged = {**CONFIDENCE_CAPS, **(caps or {})}
    for fc in analysis.get("forecasts", []) or []:
        level = fc.get("evidence_level", "article_based_hypothesis")
        cap = float(merged.get(level, merged["article_based_hypothesis"]))
        try:
            fc["confidence"] = round(min(float(fc.get("confidence", 0.0)), cap), 2)
        except (TypeError, ValueError):
            fc["confidence"] = 0.0


def analyze_record(store: LocalPrivateInsightStore, private_article_id: str,
                   config: Optional[dict] = None) -> Optional[dict]:
    """1件を分析して保存する。LLM未設定/失敗時はルールベースへフォールバック（§15, §19）。"""
    config = config or {}
    record = store.get(private_article_id)
    if record is None:
        return None
    body = store.read_body(record)
    if not body:
        record.status = "needs_review"
        record.error_code = "body_missing"
        store._upsert_meta(record)
        return None

    provider = (config.get("provider") or "").lower()
    adapters = []
    if provider in ("", "anthropic"):
        adapters.append(AnthropicAnalysisAdapter(model=config.get("model", ""),
                                                 timeout=int(config.get("timeout_seconds", 60))))
    fallback_enabled = config.get("fallback_to_rule_based", True)

    for adapter in adapters:
        if not getattr(adapter, "available", lambda: True)():
            continue
        try:
            analysis = adapter.analyze(record, body, config)
            store.save_analysis(private_article_id, analysis)
            return analysis
        except Exception:  # noqa: BLE001 LLM障害では本文を失わずfallbackする
            record.retry_count += 1
            record.error_code = "llm_error"
            store._upsert_meta(record)
            break

    if fallback_enabled:
        analysis = LocalRuleBasedFallbackAdapter().analyze(record, body, config)
        store.save_analysis(private_article_id, analysis)
        return analysis

    record.status = "needs_review"
    store._upsert_meta(record)
    return None


# ---------- 公開可能な派生情報（§12。allowlist方式） ----------

def build_derived_summary(record: PrivateInsightRecord, analysis: Optional[dict]) -> dict:
    """Published Private Insight Summary。allowlistされたキーだけを持ち、
    raw本文・長文引用・認証情報・storage key・内部エラー・プロンプトは構造的に含まれない。"""
    analysis = analysis or {}
    forecasts = analysis.get("forecasts", []) or []
    top = forecasts[0] if forecasts else {}
    themes = (analysis.get("classification") or {}).get("themes", [])
    impression = analysis.get("ai_analyst_impression") or {}
    return {
        "private_article_id": record.private_article_id,
        "title": record.title,
        "source_name": record.source_name,
        "submitted_at": record.submitted_at_jst or record.submitted_at_utc,
        "short_summary": (analysis.get("basic") or {}).get("summary_200", ""),
        "themes": themes,
        "industries": (analysis.get("market_intelligence") or {}).get("beneficiary_sectors", []),
        "sectors": (analysis.get("market_intelligence") or {}).get("beneficiary_sectors", []),
        "related_assets": (top.get("affected_markets") or [])[:5],
        "importance_score": 0.5,
        "structural_score": 0.5,
        "impression_hint": impression.get("why_important", ""),
        "forecast_summary": [
            {
                "scenario_type": f.get("scenario_type", ""),
                "scenario_title": f.get("scenario_title", ""),
                "horizon": f.get("horizon", ""),
                "confidence": f.get("confidence", 0.0),
                "leading_indicators": (f.get("leading_indicators") or [])[:4],
                "next_review_date": f.get("next_review_date", ""),
                "validation_status": f.get("validation_status", "pending"),
            }
            for f in forecasts[:4]
        ],
        "confidence": top.get("confidence", 0.0),
        "next_review_date": top.get("next_review_date", ""),
        "related_event_cluster_ids": [],
    }


DERIVED_FORBIDDEN_KEYS = {"raw_body", "body", "full_article_text", "long_quote",
                          "raw_body_storage_key", "user_note", "prompt", "api_key"}


# ---------- Worker同期（本番経路。transport注入でオフラインテスト可能） ----------

def sync_and_analyze_from_worker(api_url: str, token: str,
                                 config: Optional[dict] = None,
                                 transport: Optional[Callable] = None,
                                 max_items: int = 20) -> dict:
    """WorkerのqueueをGET→分析→結果をPOSTで返す。本文はメモリのみを通過し、
    ログ・リポジトリへ一切書き出さない（printする場合もIDと件数のみ）。"""
    import requests as _requests

    def _default_transport(method: str, url: str, payload: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if method == "GET":
            r = _requests.get(url, headers=headers, timeout=30)
        else:
            r = _requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        return r.json()

    call = transport or _default_transport
    queue = call("GET", f"{api_url}/queue?limit={max_items}") or {}
    items = queue.get("items", [])
    done = failed = 0
    for item in items:
        record = PrivateInsightRecord.from_dict({k: v for k, v in item.items() if k != "body"})
        body = item.get("body", "")
        if not body:
            failed += 1
            continue
        try:
            try:
                adapter = AnthropicAnalysisAdapter(model=(config or {}).get("model", ""))
                if adapter.available():
                    analysis = adapter.analyze(record, body, config)
                else:
                    raise RuntimeError("no_api_key")
            except Exception:  # noqa: BLE001
                analysis = LocalRuleBasedFallbackAdapter().analyze(record, body, config)
            derived = build_derived_summary(record, analysis)
            call("POST", f"{api_url}/analysis/{record.private_article_id}",
                 {"analysis": analysis, "derived": derived, "status": "completed"})
            done += 1
        except Exception:  # noqa: BLE001 1件の失敗で全体を止めない
            failed += 1
            try:
                call("POST", f"{api_url}/analysis/{record.private_article_id}",
                     {"status": "failed_analysis"})
            except Exception:  # noqa: BLE001
                pass
    return {"fetched": len(items), "analyzed": done, "failed": failed}
