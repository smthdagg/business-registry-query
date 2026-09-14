"""爱企查数据源（百度 aiqicha.baidu.com）。

接口族（免费但配额极低，日常使用需浏览器登录 Cookie）：
- 搜索    POST /s/advanceFilterAjax        q/p/s/f -> data.list[]（pid 为混淆值，ddw 映射还原）
- 股东    GET  /detail/sharesAjax          pid/p/size -> result.resultList[{subName, subRatio}]
- 对外投资 GET /detail/investajax          pid/p/size -> result.resultList[{entName, subRatio}]

免登录时响应带 limitForward.userType=nologin / remainCount=0；
登录态失效时 status!=0。故障提示会引导去 .env 配置 AQC_COOKIE。
"""

import re
import time

import requests

from .. import config
from ..utils import clean_text, is_person_name
from .base import BaseSource, SourceError

_BASE = "https://aiqicha.baidu.com"

# pid 混淆还原映射（ddw 选择映射表，逐位替换）
_PID_MAPS = {
    1: {"4": "5", "5": "4", "6": "7", "7": "6", "8": "9", "9": "8"},
    2: {"4": "6", "6": "9", "5": "8", "8": "5", "9": "7", "7": "4"},
}

# 图谱把“股东”当自然人节点，公司实体名剔除
_ENTITY_RE = re.compile(
    r"(公司|企业|中心|合伙|集团|有限|股份|研究院|研究所|事务所|工作室|"
    r"商行|银行|基金|资管|合作社|委员会|学校|医院)"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://aiqicha.baidu.com/",
    "X-Requested-With": "XMLHttpRequest",
}

# 防御式字段映射：接口字段名可能调整，命中即取
_SEARCH_FIELDS: dict[str, tuple[str, ...]] = {
    "name": ("entName", "name", "entShortName"),
    "legalPersonName": ("legalPerson", "frName", "legalPersonName"),
    "regStatus": ("entStatus", "entStatusName", "openStatus"),
    "regCapital": ("regCapital", "regCapitalStr"),
    "estiblishTime": ("openTime", "esDate", "estiblishTime"),
    "creditCode": ("creditCode", "uniscid", "unifiedCode"),
    "regNumber": ("regNo", "regNumber"),
    "industry": ("industryWord", "industry", "industryCategory"),
    "phone": ("phone", "telephone", "contactTel"),
}


def unmask_pid(pid, ddw) -> str:
    """还原爱企查混淆 pid；ddw 不在已知映射中时原样返回。"""
    text = str(pid)
    mapping = _PID_MAPS.get(int(ddw or 0))
    if not mapping:
        return text
    return "".join(mapping.get(ch, ch) for ch in text)


class AiqichaSource(BaseSource):
    """爱企查：登录 Cookie 驱动的免费源，含股东/对外投资明细。"""

    id = "aqc"
    label = "爱企查（需 Cookie）"
    description = "百度爱企查接口：搜索 + 股东 + 对外投资明细，可支撑子公司图谱。"
    notes = (
        "免费但免登录配额极低：请在 .env 配置 AQC_COOKIE"
        "（浏览器登录 aiqicha.baidu.com 后复制完整 Cookie）后重启使用。"
    )
    angles = ["综合", "企业名"]
    strategies = ["shareholder", "invest"]
    min_interval = config.AQC_DELAY
    cacheable = True

    def __init__(self) -> None:
        self._bootstrapped = bool(self.init_session(_HEADERS))
        self._last_meta: dict = {}

    def last_meta(self) -> dict:
        return self._last_meta

    def _ensure_session(self) -> None:
        """未配置 Cookie 时先访问首页获取临时会话 Cookie（仅探测用途）。"""
        if self._bootstrapped:
            return
        try:
            self._session.get(_BASE + "/", timeout=config.REQUEST_TIMEOUT)
        except requests.RequestException:
            pass
        self._bootstrapped = True

    # ---------------- 搜索 ----------------

    def search(self, keyword: str, angle: str = "综合", limit: int = 20,
               region_id: str = "") -> list[dict]:
        # aqc 无省份筛选能力，region_id 仅保持签名兼容（聚合层统一传参）
        self._bootstrapped = bool(self.refresh_cookie())
        kw = clean_text(keyword)
        if not kw:
            return []
        self._ensure_session()
        params = {"q": kw, "p": 1, "s": max(1, min(int(limit), 20)), "f": "{}"}
        try:
            resp = self._session.get(
                _BASE + "/s/advanceFilterAjax", params=params,
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.Timeout:
            raise SourceError("爱企查请求超时，请稍后重试") from None
        except requests.RequestException as e:
            raise SourceError(f"爱企查网络请求失败：{e}") from None
        except ValueError:
            raise SourceError(
                "爱企查返回非 JSON（多为风控或登录态失效，请更新 AQC_COOKIE）"
            ) from None

        if data.get("status") != 0:
            raise SourceError(
                f"爱企查接口状态异常：{data.get('status')}"
                "（多为登录态失效，请更新 AQC_COOKIE）"
            )

        data_obj = data.get("data") or {}
        self._last_meta = {"total": int(data_obj.get("total") or 0)}

        items = data_obj.get("list") or []
        if not items:
            gate = data_obj.get("limitForward") or {}
            if gate.get("userType") == "nologin" or gate.get("remainCount") == 0:
                raise SourceError(
                    "爱企查免登录配额已用尽（remainCount=0）。"
                    "请在 .env 配置 AQC_COOKIE（浏览器登录爱企查后复制完整 Cookie）后重启使用。"
                )
        ddw = data.get("ddw") or 0
        records = []
        for item in items:
            rec = self._normalize(item, ddw)
            if rec:
                records.append(rec)
        return records

    @classmethod
    def _normalize(cls, item: dict, top_ddw) -> dict | None:
        rec: dict = {}
        for key, candidates in _SEARCH_FIELDS.items():
            for field in candidates:
                value = clean_text(item.get(field))
                if value:
                    rec[key] = value
                    break
        if not rec.get("name"):
            return None
        pid = item.get("pid") or item.get("entId")
        if pid:
            rec["_pid"] = unmask_pid(pid, item.get("ddw", top_ddw))
        return rec

    # ---------------- 明细（关联分析 enrich 钩子） ----------------

    def _get_json(self, path: str, params: dict) -> dict | None:
        try:
            resp = self._session.get(
                _BASE + path, params=params, timeout=config.REQUEST_TIMEOUT
            )
            if resp.status_code != 200:
                return None
            return resp.json()
        except (requests.RequestException, ValueError):
            return None

    def enrich(self, rec: dict) -> dict:
        """补全记录的股东/对外投资明细；未配 Cookie 或请求失败时静默返回原记录。"""
        pid = rec.get("_pid")
        if not pid or "investCompanies" in rec:
            return rec
        shares = self._get_json("/detail/sharesAjax", {"pid": pid, "p": 1, "size": 20}) or {}
        time.sleep(max(0.3, self.min_interval * 0.5))
        invest = self._get_json("/detail/investajax", {"pid": pid, "p": 1, "size": 20}) or {}

        if shares.get("status") == 0:
            holders = []
            for it in (shares.get("result") or {}).get("resultList") or []:
                name = clean_text(it.get("subName") or it.get("name"))
                if not name or not is_person_name(name):
                    continue
                holders.append({
                    "name": name,
                    "percent": clean_text(it.get("subRatio") or it.get("percent")),
                })
            if holders:
                rec["shareholders"] = holders

        if invest.get("status") == 0:
            names = [
                clean_text(it.get("entName") or it.get("name"))
                for it in (invest.get("result") or {}).get("resultList") or []
            ]
            names = [n for n in names if n]
            if names:
                rec["investCompanies"] = names
        return rec
