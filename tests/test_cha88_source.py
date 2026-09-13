"""88查（阿里 MTOP）源的离线单元测试：签名、字段映射、风控报错、token 重试。"""

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.sources.base import SourceError
from app.sources.cha88 import Cha88Source, _sign

# ---------------- 签名 ----------------

def test_sign_deterministic_and_sensitive():
    a = _sign("tok", "123", "key", '{"x":1}')
    b = _sign("tok", "123", "key", '{"x":1}')
    c = _sign("tok", "124", "key", '{"x":1}')
    assert a == b and a != c
    assert len(a) == 32
    assert a == hashlib.md5("tok&123&key&{\"x\":1}".encode()).hexdigest()


# ---------------- token 提取 ----------------

def _stub_session(cookies=None, header_cookie=""):
    s = SimpleNamespace(
        cookies=[SimpleNamespace(name=n, value=v) for n, v in (cookies or [])],
        headers={"Cookie": header_cookie} if header_cookie else {},
    )
    return s


def test_token_from_cookie_jar():
    src = Cha88Source()
    src._session = _stub_session([("_m_h5_tk", "abcdef123456_1700000000000")])
    assert src._token() == "abcdef123456"


def test_token_from_manual_header():
    src = Cha88Source()
    src._session = _stub_session([], "a=1; _m_h5_tk=deadbeef_1700000000000; b=2")
    assert src._token() == "deadbeef"


def test_token_missing_returns_empty():
    src = Cha88Source()
    src._session = _stub_session()
    assert src._token() == ""


# ---------------- 字段映射 ----------------

def test_cha88_normalize_real_sample():
    item = {
        "ent_name": "<em>华为技术有限公司</em>",
        "legal_name": "赵明路",
        "social_credit_code": "914403001922038216",
        "ent_status": "存续",
        "reg_cap": "4114113.182万",
        "es_date": "1987-09-15",
        "address": "深圳市龙岗区坂田华为总部办公楼",
        "ent_type": "有限责任公司（法人独资）",
        "license_number": "440301103097413",
        "old_ent_name": "深圳市华为技术有限公司",
        "web_sites": ["www.huawei.com", "www.dbankcdn.com"],
        "ability_label_outside": "ZM$海关高级认证;高新技术企业",
        "searchType": "alias_names",
        "companyId": "1So9yiTvoXJmRsoXUr78G9OsC2idzT4pl",
    }
    rec = Cha88Source._normalize(item)
    assert rec["name"] == "华为技术有限公司"
    assert rec["legalPersonName"] == "赵明路"
    assert rec["creditCode"] == "914403001922038216"
    assert rec["regCapital"] == "4114113.182万"
    assert rec["estiblishTime"] == "1987-09-15"
    assert rec["regNumber"] == "440301103097413"
    assert rec["historyNames"] == "深圳市华为技术有限公司"
    assert "www.huawei.com" in rec["website"]
    assert rec["tags"] == ["海关高级认证", "高新技术企业"]
    assert rec["matchType"] == "曾用名匹配"
    assert rec["_pid"] == "1So9yiTvoXJmRsoXUr78G9OsC2idzT4pl"


def test_cha88_normalize_min_item():
    rec = Cha88Source._normalize({"ent_name": "星辰网吧", "companyId": "abc"})
    assert rec["name"] == "星辰网吧" and rec["_pid"] == "abc"
    assert Cha88Source._normalize({"legal_name": "张三"}) is None


# ---------------- 风控与 token 重试 ----------------

class _FakeResp:
    def __init__(self, payload):
        self._d = payload

    def json(self):
        return self._d


class _FakeSession:
    """前 N 次返回 TOKEN_EMPTY，之后返回 SUCCESS；记录签名所用 token。"""

    def __init__(self, responses, cookie_name="_m_h5_tk", cookie_value="tok123_1700"):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}
        self.cookies = [SimpleNamespace(name=cookie_name, value=cookie_value)]

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return _FakeResp(self.responses.pop(0))


def test_cha88_search_token_retry_and_parse(monkeypatch):
    src = Cha88Source()
    ok = {"ret": ["SUCCESS::调用成功"], "data": {
        "total": 20, "data": [{"ent_name": "星辰科技", "legal_name": "张三"}],
    }}
    empty = {"ret": ["FAIL_SYS_TOKEN_EMPTY::令牌为空"]}
    fake = _FakeSession([empty, ok], cookie_value="tok123_1700000000000")
    src._session = fake
    monkeypatch.setattr("app.sources.cha88.time.sleep", lambda *_: None)

    rows = src.search("星辰科技", "企业名", 1)
    assert rows[0]["name"] == "星辰科技"
    assert rows[0]["legalPersonName"] == "张三"
    assert len(fake.calls) == 2
    # 第二次请求的签名必须用刷新后的 token（同一次签名参数重算）
    import time as _t
    expected = _sign("tok123", fake.calls[1]["t"], "12574478", fake.calls[1]["data"])
    assert fake.calls[1]["sign"] == expected


def test_cha88_risk_control_message(monkeypatch):
    import unittest.mock as mock
    import app.sources.cha88 as m

    src = Cha88Source()
    fake = _FakeSession([{"ret": ["FAIL_SYS_USER_VALIDATE", "RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试"]}] * 3)
    src._session = fake

    with pytest.raises(SourceError) as exc:
        with mock.patch.object(m.time, "sleep", lambda *_: None):
            src.search("华为", "综合")
    msg = str(exc.value)
    assert "RGV587" in msg or "滑块" in msg
    assert "CHA88_COOKIE" in msg
