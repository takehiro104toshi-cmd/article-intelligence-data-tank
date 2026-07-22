"""Phase 3 Batch 1.5 テスト: EDINET / e-Stat 公式JSON APIアダプタ（§7〜§13）。

ネットワークは使わず、json_transport（(url, headers, timeout)->(status, dict)）を注入して
決定的に検証する。実APIレスポンスは呼ばない（サンドボックスは外部egress不可）。

検証観点:
  - EDINET: doc→raw 変換 / doctype_map 分類 / 未知コードは classify_disclosure フォールバック
    / 取下げ(withdrawalStatus=1)スキップ / 訂正(parentDocID)識別 / 同一docIDのdedup。
  - e-Stat: series_whitelist フィルタ / statistical_type と §6 の統計フィールド構造。
  - api_key 未設定 → failed(api_key_unset)（呼び出し側で DEGRADED/exit0＝§10）。
  - fetch_via_adapter のディスパッチ（edinet/estat/unknown）。
  - run_live_ingestion_all 経由でパイプライン全体を流し、source_metadata が
    保存済み記事まで保持される（本文は持たない）ことを確認。
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone

from tank.cursor import CursorStore
from tank.index import ArticleIndex
from tank.ingestion import run_live_ingestion_all
from tank.source_adapters import (
    fetch_edinet, fetch_estat, fetch_via_adapter, _jst_to_utc_iso,
)
from tank.storage import ArticleStore

NOW = datetime(2026, 7, 21, 3, 0, tzinfo=timezone.utc)

# config.yaml の doctype_map と同等の暫定対応表。
DOCTYPE_MAP = {
    "120": "earnings", "130": "earnings", "140": "earnings", "150": "earnings",
    "160": "earnings", "180": "other", "350": "large_shareholding",
    "360": "large_shareholding",
}
WHITELIST = ["国民経済計算", "消費者物価", "労働力調査", "鉱工業指数", "貿易統計"]


# ---------- transport ヘルパ（注入用の偽JSON） ----------

def _json_transport(status, payload):
    def transport(url, headers, timeout):
        return status, payload
    return transport


def _edinet_payload(*docs):
    return {"metadata": {"status": "200"}, "results": list(docs)}


def _edinet_doc(doc_id, code, desc, filer="テスト株式会社", sec="12340",
                withdrawal="0", parent="", submit="2026-07-21 15:00"):
    return {
        "docID": doc_id, "edinetCode": "E00001", "secCode": sec,
        "filerName": filer, "docTypeCode": code, "docDescription": desc,
        "submitDateTime": submit, "withdrawalStatus": withdrawal,
        "parentDocID": parent,
    }


# ============================================================
# EDINET
# ============================================================

def test_edinet_doc_to_raw_maps_fields_and_metadata():
    payload = _edinet_payload(_edinet_doc("S1000001", "120", "有価証券報告書"))
    r = fetch_edinet({}, "KEY", "2026-07-21", DOCTYPE_MAP,
                     transport=_json_transport(200, payload))
    assert r.status == "ok"
    assert len(r.articles) == 1
    a = r.articles[0]
    assert a["source"] == "EDINET"
    assert a["title"] == "有価証券報告書"
    assert "docID=S1000001" in a["url"]          # canonical URL に docID を埋め込む
    assert a["published_at_utc"]                  # JST 15:00 -> UTC ISO
    m = a["source_metadata"]
    assert m["adapter"] == "edinet"
    assert m["doc_id"] == "S1000001"
    assert m["doc_type_code"] == "120"
    assert m["disclosure_type"] == "earnings"     # doctype_map 由来
    assert m["security_code"] == "12340"
    assert m["is_withdrawal"] is False
    assert m["is_correction"] is False
    # 本文(body)は絶対に持たない（メタデータのみ・§8）
    assert "body" not in m and "body" not in a


def test_edinet_unknown_doctype_falls_back_to_classifier_not_hardcode():
    # 未知コード(999)。doctype_map に無い→classify_disclosure で語彙判定（§7 断定しない）。
    payload = _edinet_payload(_edinet_doc("S1000002", "999", "業績予想の修正に関するお知らせ"))
    r = fetch_edinet({}, "KEY", "2026-07-21", DOCTYPE_MAP,
                     transport=_json_transport(200, payload))
    m = r.articles[0]["source_metadata"]
    # 語彙(業績予想の修正)で guidance_revision / high materiality に分類される
    assert m["disclosure_type"] == "guidance_revision"
    assert m["materiality"] == "high"
    assert m["doc_type_code"] == "999"            # コードはそのまま保持（捏造しない）


def test_edinet_generic_180_defers_to_description_vocabulary():
    # docTypeCode 180（臨時報告書）は doctype_map で汎用 "other"。docDescription が
    # 業績予想修正なら語彙判別が優先され guidance_revision/high になる（§3: 材料を潰さない）。
    payload = _edinet_payload(
        _edinet_doc("S1000010", "180", "業績予想及び配当予想の修正に関するお知らせ"))
    r = fetch_edinet({}, "KEY", "2026-07-21", DOCTYPE_MAP,
                     transport=_json_transport(200, payload))
    m = r.articles[0]["source_metadata"]
    assert m["doc_type_code"] == "180"
    assert m["disclosure_type"] == "guidance_revision"
    assert m["materiality"] == "high"


def test_edinet_specific_doctype_map_wins_over_description():
    # 120（有報）は doctype_map=earnings が確実。docDescription に紛らわしい語があっても
    # コード対応表を優先する（§7: 確定コードは対応表が正）。
    payload = _edinet_payload(
        _edinet_doc("S1000011", "120", "有価証券報告書"))
    r = fetch_edinet({}, "KEY", "2026-07-21", DOCTYPE_MAP,
                     transport=_json_transport(200, payload))
    assert r.articles[0]["source_metadata"]["disclosure_type"] == "earnings"


def test_edinet_180_without_material_vocab_stays_other():
    # 180 で語彙判別も効かない（材料語なし）ものは other/low のまま（推測分類しない・§3）。
    payload = _edinet_payload(
        _edinet_doc("S1000012", "180", "臨時報告書"))
    r = fetch_edinet({}, "KEY", "2026-07-21", DOCTYPE_MAP,
                     transport=_json_transport(200, payload))
    m = r.articles[0]["source_metadata"]
    assert m["disclosure_type"] == "other"
    assert m["materiality"] == "low"


def test_edinet_skips_withdrawn_documents():
    payload = _edinet_payload(
        _edinet_doc("S1000003", "120", "有価証券報告書"),
        _edinet_doc("S1000004", "120", "有価証券報告書（取下げ）", withdrawal="1"),
    )
    r = fetch_edinet({}, "KEY", "2026-07-21", DOCTYPE_MAP,
                     transport=_json_transport(200, payload))
    ids = [a["source_metadata"]["doc_id"] for a in r.articles]
    assert ids == ["S1000003"]                    # 取下げは保存対象外（§9）


def test_edinet_correction_flagged_via_parent_doc_id():
    payload = _edinet_payload(
        _edinet_doc("S1000006", "130", "訂正有価証券報告書", parent="S1000005"))
    r = fetch_edinet({}, "KEY", "2026-07-21", DOCTYPE_MAP,
                     transport=_json_transport(200, payload))
    m = r.articles[0]["source_metadata"]
    assert m["is_correction"] is True
    assert m["parent_doc_id"] == "S1000005"


def test_edinet_api_key_unset_returns_failed():
    r = fetch_edinet({}, "", "2026-07-21", DOCTYPE_MAP,
                     transport=_json_transport(200, _edinet_payload()))
    assert r.status == "failed"
    assert r.error == "api_key_unset"             # §10: 呼び出し側で DEGRADED/exit0


def test_edinet_http_error_returns_failed():
    r = fetch_edinet({}, "KEY", "2026-07-21", DOCTYPE_MAP,
                     transport=_json_transport(429, None))
    assert r.status == "failed"
    assert r.http_status == 429                    # 429/403 は無理な再試行をしない


# ============================================================
# e-Stat
# ============================================================

def _estat_payload(*tables):
    return {"GET_STATS_LIST": {"RESULT": {"STATUS": 0},
                               "DATALIST_INF": {"TABLE_INF": list(tables)}}}


def _estat_table(sid, stat_name, title, updated="2026-07-20", survey="202606"):
    return {
        "@id": sid,
        "STAT_NAME": {"@code": "001", "$": stat_name},
        "TITLE": {"$": title},
        "GOV_ORG": {"$": "総務省"},
        "UPDATED_DATE": updated,
        "SURVEY_DATE": survey,
    }


def test_estat_filters_by_whitelist():
    payload = _estat_payload(
        _estat_table("0003001", "消費者物価指数", "全国 2020年基準"),
        _estat_table("0009999", "図書館調査", "貸出冊数"),   # 非対象（§4）
    )
    r = fetch_estat({}, "APPID", WHITELIST,
                    transport=_json_transport(200, payload))
    assert r.status == "ok"
    assert len(r.articles) == 1
    assert r.articles[0]["source_metadata"]["category"] == "消費者物価"


def test_estat_record_has_statistical_type_and_stats_fields():
    payload = _estat_payload(
        _estat_table("0003001", "消費者物価指数", "全国", updated="2026-07-19"))
    r = fetch_estat({}, "APPID", WHITELIST,
                    transport=_json_transport(200, payload))
    m = r.articles[0]["source_metadata"]
    assert m["adapter"] == "estat"
    assert m["statistical_type"] == "statistical_release"   # §5
    # §6 の統計フィールドが構造として存在する（値は getStatsData 未取得で空）
    for f in ("stats_data_id", "series_name", "reference_period", "released_at",
              "value", "unit", "previous_value", "revised_value",
              "revision_flag", "official_url"):
        assert f in m, f
    assert m["stats_data_id"] == "0003001"
    assert m["value"] == "" and m["unit"] == ""             # 値は未取得（構造だけ保持）


def test_estat_api_key_unset_returns_failed():
    r = fetch_estat({}, "", WHITELIST,
                    transport=_json_transport(200, _estat_payload()))
    assert r.status == "failed" and r.error == "api_key_unset"


def test_estat_single_table_object_is_normalized_to_list():
    # e-Stat は 1件のとき TABLE_INF が list ではなく dict になり得る。
    single = _estat_table("0003001", "貿易統計", "全国")
    payload = {"GET_STATS_LIST": {"DATALIST_INF": {"TABLE_INF": single}}}
    r = fetch_estat({}, "APPID", WHITELIST,
                    transport=_json_transport(200, payload))
    assert r.status == "ok" and len(r.articles) == 1


# ============================================================
# ディスパッチャ
# ============================================================

def test_fetch_via_adapter_dispatches_edinet():
    ctx = {"edinet_key": "KEY", "edinet_doctype_map": DOCTYPE_MAP}
    r = fetch_via_adapter({"adapter": "edinet"}, NOW, ctx,
                          json_transport=_json_transport(
                              200, _edinet_payload(_edinet_doc("S1", "120", "有報"))))
    assert r.status == "ok" and r.articles[0]["source"] == "EDINET"


def test_fetch_via_adapter_dispatches_estat():
    ctx = {"estat_app_id": "APPID", "estat_series_whitelist": WHITELIST}
    payload = _estat_payload(_estat_table("0003001", "労働力調査", "全国"))
    r = fetch_via_adapter({"adapter": "estat"}, NOW, ctx,
                          json_transport=_json_transport(200, payload))
    assert r.status == "ok" and r.articles[0]["source"] == "e-Stat"


def test_fetch_via_adapter_unknown_adapter_fails_gracefully():
    r = fetch_via_adapter({"adapter": "mystery"}, NOW, {})
    assert r.status == "failed" and "unknown_adapter" in r.error


def test_jst_to_utc_iso_converts_and_tolerates_garbage():
    assert _jst_to_utc_iso("2026-07-21 15:00").startswith("2026-07-21T06:00")
    assert _jst_to_utc_iso("") == ""
    assert _jst_to_utc_iso("not-a-date") == ""


# ============================================================
# パイプライン統合（source_metadata が保存記事まで保持される）
# ============================================================

def _make_env():
    root = tempfile.mkdtemp(prefix="adapter_")
    store = ArticleStore(root + "/store")
    index = ArticleIndex(root + "/store/indexes/idx.sqlite")
    cursors = CursorStore(root + "/store/cursors/c.json")
    return store, index, cursors


def test_pipeline_preserves_source_metadata_on_stored_article():
    store, index, cursors = _make_env()
    src = {"id": "edinet_disclosures", "name": "EDINET", "adapter": "edinet",
           "trust": 0.95, "country": "JP"}
    ctx = {"edinet_key": "KEY", "edinet_doctype_map": DOCTYPE_MAP}
    payload = _edinet_payload(
        _edinet_doc("SPIPE01", "120", "有価証券報告書", sec="70110"))
    res = run_live_ingestion_all(
        [src], store, index, cursors, {}, NOW,
        api_ctx=ctx, json_transport=_json_transport(200, payload))
    assert res[0]["status"] == "success"
    assert res[0]["new"] == 1
    # 保存済みシャードを読み戻し、source_metadata が保持されていることを確認
    stored = [a for p in store.all_shard_paths() for a in store.read_shard(p)]
    assert len(stored) == 1
    art = stored[0]
    assert art.source_metadata.get("doc_id") == "SPIPE01"
    assert art.source_metadata.get("disclosure_type") == "earnings"
    # 本文は保存しない（メタデータのみ）
    assert "body" not in art.source_metadata


def test_pipeline_dedups_same_doc_id_across_two_runs():
    store, index, cursors = _make_env()
    src = {"id": "edinet_disclosures", "name": "EDINET", "adapter": "edinet",
           "trust": 0.95, "country": "JP"}
    ctx = {"edinet_key": "KEY", "edinet_doctype_map": DOCTYPE_MAP}
    payload = _edinet_payload(_edinet_doc("SDUP01", "120", "有価証券報告書"))
    # 1回目
    r1 = run_live_ingestion_all([src], store, index, cursors, {}, NOW,
                                api_ctx=ctx, json_transport=_json_transport(200, payload))
    # 2回目（同一docID＝同一canonical URL）: 新規0・重複1（§9 dedup）
    r2 = run_live_ingestion_all([src], store, index, cursors, {}, NOW,
                                api_ctx=ctx, json_transport=_json_transport(200, payload))
    assert r1[0]["new"] == 1
    assert r2[0]["new"] == 0
    assert index.count() == 1


def test_pipeline_api_key_unset_is_isolated_failure_not_crash():
    # キー未設定でも例外を投げず failed で返り、他ソース/Package生成を止めない（§10）。
    store, index, cursors = _make_env()
    src = {"id": "edinet_disclosures", "name": "EDINET", "adapter": "edinet",
           "trust": 0.95, "country": "JP"}
    res = run_live_ingestion_all([src], store, index, cursors, {}, NOW,
                                 api_ctx={"edinet_key": ""},
                                 json_transport=_json_transport(200, _edinet_payload()))
    assert res[0]["status"] == "failed"
    assert index.count() == 0
