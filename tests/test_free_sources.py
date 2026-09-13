"""Cookie 源（爱企查 / 风鸟）的离线单元测试：pid 还原、字段映射、enrich 解析。"""

import pytest

from app.sources.aiqicha import AiqichaSource, is_person_name, unmask_pid
from app.sources.base import SourceError
from app.sources.riskbird import RiskbirdSource


# ---------------- pid 混淆还原 ----------------

def test_unmask_pid_ddw1():
    assert unmask_pid("456789", 1) == "547698"
    assert unmask_pid("0123", 1) == "0123"


def test_unmask_pid_ddw2():
    # ENScan_GO 文档中的样例：混淆 31390200992722 (ddw=2) -> 31370200772422
    assert unmask_pid("31390200992722", 2) == "31370200772422"
    assert unmask_pid("456789", 2) == "689457"


def test_unmask_pid_unknown_ddw_passthrough():
    assert unmask_pid("12345", 0) == "12345"
    assert unmask_pid("12345", 7) == "12345"


# ---------------- 自然人判定 ----------------

def test_is_person_name():
    assert is_person_name("张伟明") is True
    assert is_person_name("欧阳娜娜子") is True
    assert is_person_name("华为技术有限公司") is False
    assert is_person_name("星辰投资合伙企业(有限合伙)") is False
    assert is_person_name("") is False


# ---------------- 爱企查字段映射 ----------------

def test_aqc_normalize_maps_fields():
    item = {
        "entName": "华为技术有限公司",
        "pid": "31390200992722",
        "ddw": 2,
        "entStatus": "存续",
        "legalPerson": "赵国路",
        "openTime": "1987-09-15",
    }
    rec = AiqichaSource._normalize(item, 1)
    assert rec["name"] == "华为技术有限公司"
    assert rec["_pid"] == "31370200772422"  # 以条目自身 ddw=2 为准
    assert rec["legalPersonName"] == "赵国路"
    assert rec["regStatus"] == "存续"


def test_aqc_normalize_empty_returns_none():
    assert AiqichaSource._normalize({"pid": "1"}, 1) is None


# ---------------- 爱企查 enrich 解析（mock 响应） ----------------

def _aqc_source_with_mock(monkeypatch, shares, invest):
    src = AiqichaSource()
    calls = []

    def fake_get_json(path, params):
        calls.append(path)
        if path == "/detail/sharesAjax":
            return shares
        if path == "/detail/investajax":
            return invest
        return None

    monkeypatch.setattr(src, "_get_json", fake_get_json)
    return src, calls


def test_aqc_enrich_parses_details(monkeypatch):
    shares = {"status": 0, "result": {"resultList": [
        {"subName": "张三", "subRatio": "60%"},
        {"subName": "华为投资控股有限公司", "subRatio": "40%"},  # 实体股东应被剔除
    ]}}
    invest = {"status": 0, "result": {"resultList": [
        {"entName": "子公司甲有限公司"},
        {"entName": "子公司乙有限公司"},
        {"entName": ""},
    ]}}
    src, calls = _aqc_source_with_mock(monkeypatch, shares, invest)
    rec = {"name": "华为技术有限公司", "_pid": "31370200772422"}
    out = src.enrich(rec)
    assert out["shareholders"] == [{"name": "张三", "percent": "60%"}]
    assert out["investCompanies"] == ["子公司甲有限公司", "子公司乙有限公司"]
    assert set(calls) == {"/detail/sharesAjax", "/detail/investajax"}


def test_aqc_enrich_skips_without_pid(monkeypatch):
    src, calls = _aqc_source_with_mock(monkeypatch, {}, {})
    rec = {"name": "某公司"}
    assert src.enrich(rec) == rec
    assert calls == []


def test_aqc_enrich_failure_is_silent(monkeypatch):
    src, _ = _aqc_source_with_mock(monkeypatch, {"status": -14}, None)
    rec = {"name": "某公司", "_pid": "1"}
    out = src.enrich(rec)
    assert "shareholders" not in out and "investCompanies" not in out


# ---------------- 风鸟字段映射 ----------------

def test_rb_normalize_maps_fields():
    item = {
        "ENTNAME": "星辰控股集团有限公司",
        "faren": "张伟明",
        "ENTSTATUS": "存续",
        "esDate": "2008-06-18",
        "UNISCID": "913100006811234567",
        "entid": "EN_12345",
        "tels": ["021-60001001", "13800000000"],
    }
    rec = RiskbirdSource._normalize(item)
    assert rec["name"] == "星辰控股集团有限公司"
    assert rec["legalPersonName"] == "张伟明"
    assert rec["regStatus"] == "存续"
    assert rec["estiblishTime"] == "2008-06-18"
    assert rec["creditCode"] == "913100006811234567"
    assert rec["_pid"] == "EN_12345"
    assert "021-60001001" in rec["phone"]


# ---------------- 风鸟 enrich 解析（mock 响应） ----------------

def test_rb_enrich_parses_details(monkeypatch):
    src = RiskbirdSource()
    calls = []

    def fake_get(path, params):
        assert path == "/api/ent/query"
        return {"state": 2, "orderNo": "WEB_TEST_001"}

    def fake_list(order_no, extract_type):
        assert order_no == "WEB_TEST_001"
        calls.append(extract_type)
        if extract_type == "shareHolder":
            return [
                {"shaName": "李四", "fundedRatio": "35%", "subConAm": "350万"},
                {"shaName": "星辰控股集团有限公司", "fundedRatio": "65%"},
            ]
        if extract_type == "companyInvest":
            return [
                {"entName": "被投甲公司", "funderRatio": "100%"},
                {"entName": "被投乙公司", "funderRatio": "51%"},
            ]
        return []

    monkeypatch.setattr(src, "_get", fake_get)
    monkeypatch.setattr(src, "_list_api", fake_list)
    rec = {"name": "某公司", "_pid": "EN_1"}
    out = src.enrich(rec)
    assert out["shareholders"] == [{"name": "李四", "percent": "35%", "pid": ""}]
    assert out["investCompanies"] == ["被投甲公司", "被投乙公司"]
    assert calls == ["shareHolder", "companyInvest"]


def test_rb_enrich_silent_when_no_order_no(monkeypatch):
    src = RiskbirdSource()
    monkeypatch.setattr(src, "_get", lambda path, params: None)
    called = []
    monkeypatch.setattr(src, "_list_api", lambda o, e: called.append(e))
    rec = {"name": "某公司", "_pid": "EN_2"}
    out = src.enrich(rec)
    assert "shareholders" not in out and "investCompanies" not in out
    assert called == []


def test_rb_quota_error_message():
    src = RiskbirdSource()
    # 模拟游客限额：state 嵌在 data 里（实测响应结构）
    import unittest.mock as mock

    resp = mock.Mock(status_code=200)
    resp.json.return_value = {
        "code": 20000, "msg": "成功",
        "data": {"msg": "访问已达上限", "state": "limit:tourist"},
        "success": True,
    }
    with mock.patch.object(src._session, "post", return_value=resp):
        with pytest.raises(SourceError) as exc:
            src._post("/riskbird-api/newSearch", {})
    assert "游客" in str(exc.value) and "RB_COOKIE" in str(exc.value)
