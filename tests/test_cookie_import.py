"""万能 Cookie 导入解析器测试：四种格式 + 边界。"""

from app.services.cookie_import import parse_any

NETSCAPE = """# Netscape HTTP Cookie File
.88cha.com\tTRUE\t/\tTRUE\t1789336387\txlly_s\t1
#HttpOnly_.88cha.com\tTRUE\t/\tTRUE\t1789256983\t_m_h5_tk\t3112b6877b9c83d9_1789259863123
.88cha.com\tTRUE\t/\tTRUE\t1804803613\ttfstk\tgQPodkvcAmts
"""


def test_parse_netscape():
    res = parse_any(NETSCAPE)
    assert res["format"] == "netscape"
    assert res["count"] == 3
    assert "_m_h5_tk=3112b6877b9c83d9_1789259863123" in res["cookie"]
    assert ".88cha.com" in res["domains"]


def test_parse_json_cookie_editor():
    text = json_dumps([
        {"name": "BDUSS", "value": "abc123", "domain": ".baidu.com"},
        {"name": "BAIDUID", "value": "XYZ:FG=1", "domain": ".baidu.com"},
    ])
    res = parse_any(text)
    assert res["format"] == "json"
    assert res["count"] == 2
    assert "BDUSS=abc123" in res["cookie"]


def test_parse_json_cookies_wrapper():
    text = json_dumps({"cookies": [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]})
    res = parse_any(text)
    assert res["count"] == 2


def test_parse_json_name_value_map():
    res = parse_any(json_dumps({"token": "t1", "uid": "u9"}))
    assert res["count"] == 2 and "token=t1" in res["cookie"]


def test_parse_header_string():
    res = parse_any("BDUSS=abc; BAIDUID=xyz:FG=1; _m_h5_tk=tok_123")
    assert res["format"] == "header"
    assert res["count"] == 3
    assert "BDUSS=abc" in res["cookie"]


def test_parse_header_with_prefix():
    res = parse_any("Cookie: a=1; b=2")
    assert res["format"] == "header" and res["count"] == 2


def test_parse_curl_command():
    res = parse_any('curl -s "https://x.com" -H "Cookie: foo=bar; baz=qux" -o out.txt')
    assert res["format"] == "curl"
    assert "foo=bar" in res["cookie"] and "baz=qux" in res["cookie"]


def test_parse_unknown_garbage():
    res = parse_any("这是一段没有cookie的普通文字")
    assert res["format"] == "unknown" and res["count"] == 0
    res = parse_any("")
    assert res["format"] == "empty"


def test_parse_later_value_wins():
    res = parse_any("a=1; a=2")
    assert "a=2" in res["cookie"] and "a=1" not in res["cookie"]


def json_dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)
