"""88查数据源（88cha.com，阿里巴巴出品，免账号、免登录）。

接口：阿里 MTOP 网关
  GET https://acs-m.88cha.com/h5/mtop.com.alibaba.business.query.getcompanybykeywords/2.0/
  data={"keyword":..., "scene":"companySearch", "pageNo":1, "pageSize":20, "action":""}
  签名：md5(token + '&' + t + '&' + appKey + '&' + data)，token 取 Cookie _m_h5_tk 前段。

能力：单次 20 条 + 分页，字段含法人/信用代码/注册资本/成立/地址/类型/注册号/
曾用名/官网/标签，是免费源里信息最全的。

风控说明：MTOP 有滑块风控（RGV587），纯脚本直连会被拦；用浏览器打开
88cha.com 随便搜一次后，把完整 Cookie 配到 CHA88_COOKIE 即可正常使用
（无需注册账号，Cookie 过期重新复制一次）。
"""

import hashlib
import json
import time

import requests

from .. import config
from ..utils import clean_text, extract_city, join_list
from .base import BaseSource, SourceError

_HOST = "https://acs-m.88cha.com"
_API = "mtop.com.alibaba.business.query.getcompanybykeywords"
_V = "2.0"
_APP_KEY = "12574478"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://88cha.com/",
}

# 搜索命中方式 -> 展示文案
_MATCH_TYPE = {
    "ent_name": "企业名称匹配",
    "alias_names": "曾用名匹配",
    "pinyin": "拼音匹配",
    "keyword": "关键词匹配",
}

_SEARCH_FIELDS: dict[str, tuple[str, ...]] = {
    "name": ("ent_name", "entName", "name"),
    "legalPersonName": ("legal_name", "legalPersonName"),
    "regStatus": ("ent_status", "entStatus"),
    "regCapital": ("reg_cap", "regCapital"),
    "estiblishTime": ("es_date", "esDate"),
    "regLocation": ("address", "regLocation"),
    "creditCode": ("social_credit_code", "creditCode"),
    "regNumber": ("license_number", "regNumber"),
    "companyOrgType": ("ent_type", "companyOrgType"),
    "historyNames": ("old_ent_name", "historyNames"),
    "website": ("web_sites", "webSites"),
    "phone": ("phone",),
    "email": ("email",),
}


def _sign(token: str, t: str, app_key: str, data: str) -> str:
    """MTOP H5 标准签名：md5(token&t&appKey&data)。"""
    return hashlib.md5(f"{token}&{t}&{app_key}&{data}".encode("utf-8")).hexdigest()


class Cha88Source(BaseSource):
    """88查：阿里 MTOP 接口，免账号，需浏览器 Cookie 过风控。"""

    id = "c88"
    label = "88查（免账号）"
    description = "阿里系 88查接口：单次 20 条 + 分页，字段全（法人/信用代码/注册资本/官网等）。"
    notes = (
        "免注册账号，但有滑块风控：请用浏览器打开 88cha.com 随便搜一次，"
        "把 Cookie 复制到 .env 的 CHA88_COOKIE 后重启使用。"
    )
    angles = ["综合", "企业名"]
    strategies = []  # 明细/股权接口需登录，暂不做关联展开
    min_interval = config.CHA88_DELAY
    cacheable = True

    def __init__(self) -> None:
        self.init_session(_HEADERS)
        self._last_meta: dict = {}

    def last_meta(self) -> dict:
        return self._last_meta

    # ---------------- MTOP ----------------

    def _token(self) -> str:
        jar = getattr(self._session, "cookies", None)
        for cookie in jar or []:
            if cookie.name == "_m_h5_tk":
                return cookie.value.split("_")[0]
        raw = self._session.headers.get("Cookie", "")
        if "_m_h5_tk=" in raw:
            return raw.split("_m_h5_tk=")[1].split("_")[0]
        return ""

    def _sync_token_header(self) -> None:
        """把服务器 Set-Cookie 刷新后的 _m_h5_tk 同步回手动 Cookie 头。
        否则手动头的旧 token 会一直覆盖 Jar 里的新 token，续签永不生效。"""
        jar_token = jar_enc = None
        for cookie in self._session.cookies:
            if cookie.name == "_m_h5_tk":
                jar_token = cookie.value
            elif cookie.name == "_m_h5_tk_enc":
                jar_enc = cookie.value
        if not jar_token:
            return
        hdr = self._session.headers.get("Cookie", "")
        parts = [p for p in hdr.split("; ") if p and not p.startswith("_m_h5_tk=") and not p.startswith("_m_h5_tk_enc=")]
        parts.append("_m_h5_tk=" + jar_token)
        if jar_enc:
            parts.append("_m_h5_tk_enc=" + jar_enc)
        self._session.headers["Cookie"] = "; ".join(parts)

    def _mtop_call(self, data_obj: dict) -> dict:
        payload = json.dumps(data_obj, ensure_ascii=False, separators=(",", ":"))
        last_ret = ""
        for _ in range(3):
            self._sync_token_header()
            t = str(int(time.time() * 1000))
            params = {
                "jsv": "2.5.8", "appKey": _APP_KEY, "t": t,
                "sign": _sign(self._token(), t, _APP_KEY, payload),
                "api": _API, "v": _V, "dataType": "json",
                "timeout": "20000", "type": "originaljson", "data": payload,
            }
            try:
                resp = self._session.get(
                    f"{_HOST}/h5/{_API}/{_V}/", params=params,
                    timeout=config.REQUEST_TIMEOUT,
                )
                data = resp.json()
            except requests.Timeout:
                raise SourceError("88查请求超时，请稍后重试") from None
            except (requests.RequestException, ValueError) as e:
                raise SourceError(f"88查请求失败：{e}") from None

            ret = (data.get("ret") or [""])[0]
            if ret.startswith("SUCCESS"):
                return data
            last_ret = ret
            if "TOKEN" in ret:
                # 首次调用/过期：响应会通过 Set-Cookie 下发新 _m_h5_tk，重算签名重试
                continue
            if "USER_VALIDATE" in ret or "RGV587" in ret:
                raise SourceError(
                    "88查触发滑块风控（RGV587）。请用浏览器打开 88cha.com 随便搜索一次，"
                    "然后把完整 Cookie 复制到 .env 的 CHA88_COOKIE 后重启使用。"
                )
        raise SourceError(f"88查接口失败：{last_ret}")

    # ---------------- 搜索 ----------------

    MAX_PAGES = 25  # 单次查询最多翻 25 页（20 条/页 = 500 条），防止滥用

    def search(self, keyword: str, angle: str = "综合", limit: int = 20,
               region_id: str = "") -> list[dict]:
        self.refresh_cookie()
        kw = clean_text(keyword)
        if not kw:
            return []
        want = max(1, min(int(limit), 1000))
        records: list[dict] = []
        page = 1
        total = 0
        while page <= self.MAX_PAGES and len(records) < want:
            data = self._mtop_call({
                "keyword": kw, "scene": "companySearch",
                "pageNo": page, "pageSize": 20, "action": "",
            })
            inner = data.get("data") or {}
            if total == 0:
                total = int(inner.get("total") or 0)
            items = inner.get("data") or []
            if not items:
                break
            for item in items:
                rec = self._normalize(item)
                if rec:
                    records.append(rec)
            if len(records) >= want:
                break
            page += 1
        # 填满条数预算即视为可能还有更多
        self._last_meta = {"total": total, "truncated": len(records) >= want}
        return records[:want]

    @classmethod
    def _normalize(cls, item: dict) -> dict | None:
        rec: dict = {}
        for key, candidates in _SEARCH_FIELDS.items():
            for field in candidates:
                value = item.get(field)
                if isinstance(value, list):
                    value = join_list(value)
                else:
                    value = clean_text(value)
                if value:
                    rec[key] = value
                    break
        if not rec.get("name"):
            return None
        rec["name"] = clean_text(rec["name"]).replace("<em>", "").replace("</em>", "")
        if not rec.get("city") and rec.get("regLocation"):
            rec["city"] = extract_city(rec["regLocation"])
        # 命中依据
        mt = _MATCH_TYPE.get(clean_text(item.get("searchType")))
        if mt:
            rec["matchType"] = mt
        # 标签（ZM$ 前缀为内部标记，去掉）
        labels = [
            clean_text(x).removeprefix("ZM$")
            for x in clean_text(item.get("ability_label_outside")).split(";")
        ]
        labels = [x for x in labels if x]
        if labels:
            rec["tags"] = labels
        if item.get("companyId"):
            rec["_pid"] = clean_text(item["companyId"])
        return rec
