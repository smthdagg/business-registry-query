"""万能 Cookie 导入解析器。

自动识别并解析四种常见格式，统一规范化为 "name=value; ..." 的 Cookie 头字符串：

1. Netscape HTTP Cookie File（浏览器插件导出，含 #HttpOnly_ 前缀变体）
2. JSON（浏览器插件 Cookie-Editor 风格的数组 / {"cookies": [...]} / 纯 name→value 映射）
3. Cookie 头字符串（"a=b; c=d"，容忍 "Cookie:" 前缀）
4. cURL 命令行（提取 -H "Cookie: ..." / -b "..." / --cookie）

解析策略：值以后出现的为准（同名 cookie 后值覆盖前值）；
无法识别任何 name=value 的内容返回 unknown，由调用方提示。
"""

import json
import re
from urllib.parse import unquote

MAX_TEXT = 512 * 1024  # 512KB 上限，防滥用


def _norm_pairs(pairs: list[tuple[str, str]]) -> tuple[str, int, list[str]]:
    merged: dict[str, str] = {}
    order: list[str] = []
    names: list[str] = []
    for name, value in pairs:
        name = name.strip()
        value = value.strip()
        if not name or name.lower() in ("expires", "path", "domain", "httponly",
                                         "secure", "samesite", "max-age", "comment"):
            continue
        if value == "":
            continue
        if name in merged:
            merged[name] = value
        else:
            merged[name] = value
            order.append(name)
            names.append(name)
    cookie = "; ".join(f"{n}={merged[n]}" for n in order)
    return cookie, len(order), names


def _from_items(items: list[dict], fmt: str, domains: list[str]) -> dict:
    pairs = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name") or it.get("Name") or it.get("cookieName")
        value = it.get("value") or it.get("Value") or it.get("cookieValue")
        if name and value not in (None, "", "undefined"):
            pairs.append((str(name), str(value)))
        dom = it.get("domain") or it.get("Domain")
        if dom:
            domains.append(str(dom))
    cookie, count, names = _norm_pairs(pairs)
    return {"format": fmt, "cookie": cookie, "count": count,
            "names": names, "domains": sorted(set(domains))}


def _from_netscape(text: str) -> dict | None:
    pairs: list[tuple[str, str]] = []
    domains: list[str] = []
    hit = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        hit = True
        domain, name, value = parts[0].strip(), parts[5].strip(), parts[6].strip()
        if not name or value in ("", "undefined"):
            continue
        domains.append(domain)
        pairs.append((name, value))
    if not hit:
        return None
    cookie, count, names = _norm_pairs(pairs)
    return {"format": "netscape", "cookie": cookie, "count": count,
            "names": names, "domains": sorted(set(domains))}


def _from_json(obj) -> dict | None:
    items: list = []
    domains: list[str] = []
    if isinstance(obj, dict) and isinstance(obj.get("cookies"), list):
        items = obj["cookies"]
    elif isinstance(obj, list):
        items = [x for x in obj if isinstance(x, dict)]
    elif isinstance(obj, dict):
        if all(isinstance(v, (str, int, float)) for v in obj.values()) and obj:
            items = [{"name": k, "value": str(v)} for k, v in obj.items()]
        else:
            for v in obj.values():
                if isinstance(v, list) and v and isinstance(v[0], dict) \
                        and ("name" in v[0] or "cookieName" in v[0]):
                    items = v
                    break
    if not items:
        return None
    res = _from_items(items, "json", domains)
    return res if res["count"] else None


def _from_header(text: str) -> dict | None:
    s = text.strip()
    m = re.search(r"Cookie:\s*(.+)", s, re.I)
    if m:
        s = m.group(1)
    if "=" not in s:
        return None
    pairs: list[tuple[str, str]] = []
    domains: list[str] = []
    for seg in s.split(";"):
        seg = seg.strip()
        if "=" not in seg:
            continue
        name, _, value = seg.partition("=")
        name = name.strip()
        value = value.strip()
        if not name or " " in name:
            continue
        pairs.append((name, unquote(value) if "%" in value else value))
    if not pairs:
        return None
    cookie, count, names = _norm_pairs(pairs)
    return {"format": "header", "cookie": cookie, "count": count, "names": names,
            "domains": domains}


def _from_curl(text: str) -> dict | None:
    chunks = re.findall(r'''(?:-H\s*|--header\s*)(?:["'])(Cookie:\s*[^"']+)(?:["'])''',
                        text, re.I)
    chunks += re.findall(r'''(?:-b\s*|--cookie\s*)(?:["'])([^"']+)(?:["'])''', text)
    for chunk in chunks:
        s = chunk.split('"')[0]  # 截断 curl 尾巴（如 -o out.txt）
        m = re.search(r"Cookie:\s*(.+)", s, re.I)
        if m:
            s = m.group(1)
        if "=" in s:
            res = _from_header(s)
            if res and res["count"]:
                res["format"] = "curl"
                return res
    return None


def parse_any(text: str) -> dict:
    """任意格式 Cookie 文本 → {"format","cookie","count","names","domains"}。"""
    text = (text or "").strip()
    if not text:
        return {"format": "empty", "cookie": "", "count": 0, "names": [], "domains": []}
    if len(text) > MAX_TEXT:
        return {"format": "too-large", "cookie": "", "count": 0, "names": [], "domains": []}

    try:
        obj = json.loads(text)
        res = _from_json(obj)
        if res and res["count"]:
            return res
    except Exception:
        pass

    if text.lstrip().lower().startswith("curl") or "-H \"Cookie:" in text or "-b \"" in text:
        res = _from_curl(text)
        if res and res["count"]:
            return res

    res = _from_netscape(text)
    if res and res["count"]:
        return res

    res = _from_header(text)
    if res and res["count"]:
        return res

    return {"format": "unknown", "cookie": "", "count": 0, "names": [], "domains": []}
