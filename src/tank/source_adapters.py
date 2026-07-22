"""API Source Adapter Layer（Phase 3 Batch 1.5）。

RSS/Atom 専用だった取得層を拡張し、公式JSON APIソース（EDINET / e-Stat）を
「既存パイプラインが食う生記事dict」へ正規化して供給する共通アダプタ。

設計方針（§5, §8, §12）:
  - 各アダプタは fetcher.FetchResult を返す（RSSと共通の戻り値）。
  - FetchResult.articles の各要素は build_article_from_raw が期待する形
    （title / url / description / published_at_utc / published_raw / source）に加え、
    "source_metadata" dict（開示/統計の構造化情報）を持つ。
  - よって正規化・dedup・cluster・shard保存・Index・retention・Package・run_status・
    exit code・Private Insight は**すべて既存のまま**流れる（下流は無改修）。
  - HTMLスクレイピングはしない。公式JSON APIのみ（§9）。
  - APIキー未設定なら status="failed"(error="api_key_unset") を返し、呼び出し側で
    DEGRADED/exit 0 として他ソース・Package生成を継続する（§10）。
  - EDINETのdocTypeCode分類はハードコードせず config の対応表で解決し、未知コードは
    "other" にフォールバックする（§7: 未確認コードを断定しない）。

★重要（未検証）★ サンドボックスは外部egress制限のため、実APIレスポンスでの検証は
できていません。JSONのキー名は公式ドキュメント準拠の想定で、複数キー名を許容し欠損に
耐える防御的実装にしています。実運用前に実レスポンスで doctype_map 等を確認してください。
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Tuple

from .fetcher import FetchResult

_JST = timezone(timedelta(hours=9))

# json_transport(url, headers, timeout) -> (status_code, parsed_json_or_None)
JsonTransport = Callable[[str, dict, int], Tuple[int, Optional[dict]]]


def _default_json_transport(url: str, headers: dict, timeout: int) -> Tuple[int, Optional[dict]]:
    """既定のJSON取得。requestsでGETしてJSONへparse（失敗時は(status, None)）。"""
    import requests
    resp = requests.get(url, headers=headers, timeout=timeout)
    try:
        return resp.status_code, resp.json()
    except (ValueError, _json.JSONDecodeError):
        return resp.status_code, None


def _jst_to_utc_iso(value: str) -> str:
    """'YYYY-MM-DD HH:MM(:SS)'（JST想定）を UTC ISO8601 へ。解析不能は空文字。"""
    if not value:
        return ""
    v = value.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(v, fmt).replace(tzinfo=_JST)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    # ISO8601（TZ付き）ならそのまま解釈
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_JST)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


def _first(d: dict, *keys, default=""):
    """dict から最初に見つかった非空キーの値を返す（APIのキー名ゆれに対応）。"""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


# ============================================================
# EDINET（金融庁 法定開示 API v2）
# ============================================================
# 責務（§2, §3）: 法定開示・定期報告(有報/四半期/半期)・臨時報告書・大量保有報告の取得。
#   EDINETは TDnet／適時開示（決算短信・業績予想修正の適時開示 等）の代替ではない。
#   それらは EDINET では「未取得」であり、本アダプタでは推測分類しない。

EDINET_LIST_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"


def _edinet_doc_to_raw(doc: dict, doctype_map: dict) -> Optional[dict]:
    """EDINET documents.json の1件(results要素)を生記事dictへ変換する（メタデータのみ・本文不取得）。"""
    doc_id = _first(doc, "docID", "docId")
    if not doc_id:
        return None
    filer = _first(doc, "filerName", "issuerName")
    desc = _first(doc, "docDescription")
    sec_code = _first(doc, "secCode", "securityCode")
    doc_type_code = str(_first(doc, "docTypeCode", default=""))
    submit = _first(doc, "submitDateTime", "submitDatetime", "submitDate")
    period_start = _first(doc, "periodStart")
    period_end = _first(doc, "periodEnd")
    # 訂正/取下げの識別（§9）: withdrawalStatus="1"は取下げ、docTypeCodeの訂正系や
    #   parentDocID の有無で訂正提出を識別。断定せず metadata に残す。
    withdrawal = str(_first(doc, "withdrawalStatus", default="0"))
    parent = _first(doc, "parentDocID", "parentDocId")

    # 分類（§3, §7）: config の doctype_map を優先。ただし map が汎用の "other"
    # （例: 180=臨時報告書。M&A/予想修正等を含み得る）に落ちる場合のみ docDescription
    # 語彙で判別する。未知コード（map外）も語彙判別へ委ねる。断定せず、判別できなければ
    # other/low のまま残す（＝推測で決算短信等へ強制分類しない）。
    from .source_portfolio import classify_disclosure
    mapped = doctype_map.get(doc_type_code, "")
    by_text = classify_disclosure(desc, doc_type_code)
    if mapped and mapped != "other":
        disclosure_type = mapped                      # 有報/大量保有等はコード対応表が確実
    else:
        disclosure_type = by_text.get("disclosure_type", mapped or "other")
    materiality = by_text.get("materiality", "low")

    title = desc or f"{filer}（{doc_type_code}）".strip("（）")
    # canonical URL に docID を埋め、同一docIDが自然にdedupされるようにする（§9）。
    official_url = f"https://disclosure2.edinet-fsa.go.jp/WEEK0010.aspx?docID={doc_id}"
    return {
        "title": title,
        "url": official_url,
        "description": desc[:400],
        "published_at_utc": _jst_to_utc_iso(submit),
        "published_raw": submit,
        "source": "EDINET",
        "source_metadata": {
            "adapter": "edinet",
            "doc_id": doc_id,
            "edinet_code": _first(doc, "edinetCode"),
            "security_code": sec_code,
            "company_name": filer,
            "doc_type_code": doc_type_code,
            "disclosure_type": disclosure_type,
            "materiality": materiality,
            "period_start": period_start,
            "period_end": period_end,
            "is_withdrawal": withdrawal == "1",
            "is_correction": bool(parent),
            "parent_doc_id": parent,
            "official_url": official_url,
        },
    }


def fetch_edinet(source_cfg: dict, api_key: str, date: str, doctype_map: dict,
                 transport: Optional[JsonTransport] = None, timeout: int = 12,
                 max_items: int = 2000) -> FetchResult:
    """EDINET の指定日の開示一覧(メタデータ)を取得して FetchResult を返す。

    api_key（Subscription-Key）が空なら failed(api_key_unset) を返す（§10）。
    取下げ(withdrawalStatus=1)は保存対象から除外する。PDF/XBRL本文は取得しない（§8）。
    """
    if not api_key:
        return FetchResult(status="failed", error="api_key_unset")
    transport = transport or _default_json_transport
    url = f"{EDINET_LIST_URL}?date={date}&type=2&Subscription-Key={api_key}"
    headers = {"Accept": "application/json"}
    try:
        status, payload = transport(url, headers, timeout)
    except Exception as exc:  # noqa: BLE001
        return FetchResult(status="failed", http_status=0, error=f"{type(exc).__name__}: {str(exc)[:100]}")
    if status != 200 or not isinstance(payload, dict):
        return FetchResult(status="failed", http_status=status, error=f"http_{status}")

    results = payload.get("results") or []
    raws: List[dict] = []
    for doc in results:
        if not isinstance(doc, dict):
            continue
        if str(doc.get("withdrawalStatus", "0")) == "1":
            continue  # 取下げは保存しない
        raw = _edinet_doc_to_raw(doc, doctype_map)
        if raw:
            raws.append(raw)
        if len(raws) >= max_items:
            break
    return FetchResult(status="ok", http_status=200, articles=raws)


# ============================================================
# e-Stat（政府統計の総合窓口 API v3.0）
# ============================================================
# 責務（§4）: 記事数を増やすSourceではなく「マクロ統計データSource」。
#   影響の大きい系列（GDP/CPI/雇用/消費/鉱工業生産/貿易/家計/企業/人口/住宅建設）に限定。

ESTAT_LIST_URL = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsList"


def _matches_whitelist(name: str, whitelist: List[str]) -> str:
    """統計名がホワイトリストのどのカテゴリに該当するか（該当語を含めば返す・無ければ""）。"""
    n = name or ""
    for kw in whitelist:
        if kw and kw in n:
            return kw
    return ""


def fetch_estat(source_cfg: dict, app_id: str, series_whitelist: List[str],
                updated_date: str = "", transport: Optional[JsonTransport] = None,
                timeout: int = 12, max_items: int = 200) -> FetchResult:
    """e-Stat getStatsList で「最近更新された対象統計表」を取得して FetchResult を返す。

    app_id が空なら failed(api_key_unset)（§10）。実データ(getStatsData)は取得せず、
    まず更新検知＝statistical_release レコードを作る（§4, §5）。全統計は取得しない。
    """
    if not app_id:
        return FetchResult(status="failed", error="api_key_unset")
    transport = transport or _default_json_transport
    url = f"{ESTAT_LIST_URL}?appId={app_id}&limit={max_items}"
    if updated_date:
        url += f"&updatedDate={updated_date}"
    headers = {"Accept": "application/json"}
    try:
        status, payload = transport(url, headers, timeout)
    except Exception as exc:  # noqa: BLE001
        return FetchResult(status="failed", http_status=0, error=f"{type(exc).__name__}: {str(exc)[:100]}")
    if status != 200 or not isinstance(payload, dict):
        return FetchResult(status="failed", http_status=status, error=f"http_{status}")

    # getStatsList のネスト: GET_STATS_LIST.DATALIST_INF.TABLE_INF[]
    try:
        tables = payload["GET_STATS_LIST"]["DATALIST_INF"]["TABLE_INF"]
    except (KeyError, TypeError):
        tables = []
    if isinstance(tables, dict):
        tables = [tables]

    raws: List[dict] = []
    for t in tables:
        if not isinstance(t, dict):
            continue
        stats_data_id = str(_first(t, "@id", "id"))
        stat_name = _first(t, "STAT_NAME")
        if isinstance(stat_name, dict):
            stat_name = stat_name.get("$", "")
        title_obj = _first(t, "TITLE")
        title_txt = title_obj.get("$", "") if isinstance(title_obj, dict) else str(title_obj)
        gov_org = _first(t, "GOV_ORG")
        if isinstance(gov_org, dict):
            gov_org = gov_org.get("$", "")
        series_full = f"{stat_name} {title_txt}".strip()
        matched = _matches_whitelist(series_full, series_whitelist)
        if not matched:
            continue  # 影響の大きい対象系列以外はスキップ（§4）
        updated = _first(t, "UPDATED_DATE", "UPDATE_DATE")
        survey_date = str(_first(t, "SURVEY_DATE"))
        official_url = f"https://www.e-stat.go.jp/dbview?sid={stats_data_id}"
        if not stats_data_id:
            continue
        raws.append({
            "title": f"[統計更新] {series_full}".strip(),
            "url": official_url,
            "description": series_full[:400],
            "published_at_utc": _jst_to_utc_iso(updated),
            "published_raw": updated,
            "source": "e-Stat",
            "source_metadata": {
                "adapter": "estat",
                "statistical_type": "statistical_release",   # §5
                "stats_data_id": stats_data_id,               # §6
                "series_name": series_full,
                "reference_period": survey_date,
                "released_at": _jst_to_utc_iso(updated),
                "value": "",              # getStatsData未取得のため空（構造は保持）
                "unit": "",
                "previous_value": "",
                "revised_value": "",
                "revision_flag": False,
                "category": matched,
                "gov_org": gov_org,
                "official_url": official_url,
            },
        })
        if len(raws) >= max_items:
            break
    return FetchResult(status="ok", http_status=200, articles=raws)


# ============================================================
# 共通ディスパッチャ
# ============================================================

def fetch_via_adapter(source_cfg: dict, now: datetime, api_ctx: dict,
                      json_transport: Optional[JsonTransport] = None,
                      timeout: int = 12) -> FetchResult:
    """source_cfg["adapter"] に応じて RSS 以外の取得を行う（rss は呼び出し側で従来処理）。

    api_ctx: {"edinet_key":..., "estat_app_id":..., "edinet_doctype_map":{...},
              "estat_series_whitelist":[...]}。キー未設定は各アダプタが failed(api_key_unset)。
    """
    adapter = (source_cfg.get("adapter") or "rss").lower()
    if adapter == "edinet":
        date = now.astimezone(_JST).strftime("%Y-%m-%d")
        return fetch_edinet(source_cfg, api_ctx.get("edinet_key", ""), date,
                            api_ctx.get("edinet_doctype_map", {}),
                            transport=json_transport, timeout=timeout,
                            max_items=int(source_cfg.get("max_items_per_fetch", 2000)))
    if adapter == "estat":
        return fetch_estat(source_cfg, api_ctx.get("estat_app_id", ""),
                           api_ctx.get("estat_series_whitelist", []),
                           transport=json_transport, timeout=timeout,
                           max_items=int(source_cfg.get("max_items_per_fetch", 200)))
    return FetchResult(status="failed", error=f"unknown_adapter:{adapter}")
