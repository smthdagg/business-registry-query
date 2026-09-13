"""天眼查实时源。

实现完全沿用原单文件脚本（工商查询.py）的调用方式：
- 接口：m.tianyancha.com/proxyPeers/getCompanyPhone.json（可用 TYC_URL 覆盖）
- 参数：cate / baseCode / base / key
- 请求头：与原脚本一致，仅在此基础上附加可选的 Cookie

常见“查不到”的原因与对策（错误信息会给出具体线索）：
- 返回非 JSON / HTML 页面：多为移动站风控或 JS 校验，需要有效 Cookie（TYC_COOKIE）
- 超时 / 连接失败：网络不通（海外 VPS 或代理被墙），换网络或国内 VPS
- 接口变更：可把可用的新接口地址填到 TYC_URL
"""

import re
import time
from datetime import datetime

import requests

from .. import config
from ..utils import clean_text, join_list, phones_equal
from .base import BaseSource, SourceError

# 与原 py 文件完全一致的请求头（UA 为脚本原值）
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

_get_url = lambda: getattr(config, "TYC_URL", "") or (
    "https://m.tianyancha.com/proxyPeers/getCompanyPhone.json"
)


# 实时源字段：展示键 -> 接口字段名
_FIELD_MAP: dict[str, str] = {
    "name": "name",
    "creditCode": "creditCode",
    "regNumber": "regNumber",
    "orgNumber": "orgNumber",
    "legalPersonName": "legalPersonName",
    "regCapital": "regCapital",
    "estiblishTime": "estiblishTime",
    "regStatus": "regStatus",
    "companyOrgType": "companyOrgType",
    "regLocation": "regLocation",
    "industry": "categoryStr",
    "registerInstitute": "registerInstitute",
    "businessScope": "businessScope",
    "companyScale": "companyScale",
    "historyNames": "historyNames",
    "phone": "phone",
    "city": "city",
    "district": "district",
    "companyScore": "companyScore",
    "email": "email",
    "website": "website",
}

_MULTI_SEP = re.compile(r"\s*;\s*")


def _multi(value: str) -> str:
    """接口里多值字段用 ';'(常带 \t) 分隔，统一替换成顿号，展示更干净。"""
    return _MULTI_SEP.sub("、", value)


def _to_date(value) -> str:
    """成立时间：时间戳(秒/毫秒)或 '2021-11-29 00:00:00.0' 统一成 YYYY-MM-DD。"""
    if value is None or value == "":
        return ""
    text = clean_text(value)
    if text.isdigit():
        ms = int(text)
        if ms > 10**12:
            ms //= 1000
        try:
            return datetime.fromtimestamp(ms).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return text
    text = text.replace("/", "-").replace(".", "-")
    # 形如 2021-11-29 00:00:00-0 -> 只取日期部分
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


class TianyanchaSource(BaseSource):
    """天眼查移动站搜索接口（实时）。"""

    id = "tyc"
    label = "天眼查实时接口"
    description = "调用天眼查移动站搜索接口查询真实工商公示数据（最佳努力）。"
    notes = (
        "接口偶发风控，若提示非 JSON 请在 .env 配置 TYC_COOKIE（浏览器登录后复制 Cookie）"
        "后重启；建议国内网络环境使用。"
    )
    angles = ["综合", "企业名", "法人", "信用代码", "电话"]
    strategies = ["legal", "phone"]
    min_interval = config.TYC_DELAY
    cacheable = True

    def __init__(self) -> None:
        self.init_session(_HEADERS)

    # -- 请求 --------------------------------------------------------------

    def search(self, keyword: str, angle: str = "综合", limit: int = 20,
               region_id: str = "") -> list[dict]:
        self.refresh_cookie()
        if angle not in self.angles:
            angle = "综合"
        last_error: Exception | None = None
        for attempt in (1, 2):  # 网络抖动重试一次
            try:
                records, total = self._request_once(keyword)
                self._last_meta = {"total": total, "key": keyword}
                return self._pick(records, keyword, angle, limit)
            except SourceError as e:
                last_error = e
                if attempt == 1:
                    time.sleep(max(0.5, self.min_interval))
        raise SourceError(f"天眼查接口请求失败：{last_error}")

    def last_meta(self) -> dict:
        return getattr(self, "_last_meta", {})

    def _request_once(self, keyword: str) -> tuple[list[dict], int]:
        params = {"cate": "", "baseCode": "", "base": "", "key": keyword}
        try:
            resp = self._session.get(_get_url(), params=params, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.Timeout:
            raise SourceError(
                f"请求超时（{config.REQUEST_TIMEOUT:.0f} 秒无响应）。"
                "多为网络不通（海外 VPS/代理常被拦截），建议换网络或国内服务器。"
            ) from None
        except requests.RequestException as e:
            raise SourceError(f"网络请求失败：{e}") from None

        try:
            data = resp.json()
        except ValueError:
            # 非 JSON：多半是风控页 / JS 校验 / 需要登录
            snippet = clean_text(resp.text)[:60]
            raise SourceError(
                f"接口返回非 JSON（HTTP {resp.status_code}，内容片段：{snippet!r}）。"
                "通常需要有效 Cookie：在 .env 配置 TYC_COOKIE 后重启；"
                "如仍不行可在 TYC_URL 指向可用接口。"
            ) from None

        # 原脚本按 state=='ok' 判断；这里放宽为“有 items 就算成功”，兼容接口改版
        state = data.get("state")
        data_obj = data.get("data") if isinstance(data.get("data"), dict) else {}
        items = data_obj.get("items")
        if state != "ok" and not items:
            raise SourceError(
                f"接口状态异常：state={state!r}（HTTP {resp.status_code}）。"
                "请检查 TYC_COOKIE 是否有效或接口是否变更（可用 TYC_URL 指定新地址）。"
            )

        total = int(data_obj.get("total") or 0)
        return (
            [self._normalize(item) for item in items if isinstance(item, dict)],
            total,
        )

    @staticmethod
    def _normalize(item: dict) -> dict:
        rec: dict = {}
        for key, field in _FIELD_MAP.items():
            value = item.get(field)
            if isinstance(value, list):
                value = join_list(value)
            elif key in ("phone", "companyOrgType", "businessScope", "historyNames"):
                value = _multi(clean_text(value)) if clean_text(value) else ""
            elif key == "estiblishTime":
                value = _to_date(value)
            else:
                value = clean_text(value)
            if value not in ("", None):
                rec[key] = value
        rec["name"] = clean_text(rec.get("name")) or clean_text(item.get("companyName") or "")
        # 命中依据（法定代表人匹配/股东匹配/公司名称匹配…），关联分析据此判断真伪
        mt = clean_text(item.get("matchType"))
        if mt:
            rec["matchType"] = mt
        # 标签
        tags = []
        for t in item.get("labelListV2") or []:
            t = clean_text(t)
            if t:
                tags.append(t)
        if tags:
            rec["tags"] = tags
        return rec

    @staticmethod
    def _pick(
        records: list[dict], keyword: str, angle: str, limit: int
    ) -> list[dict]:
        """接口是全站模糊搜索，这里按角度做客户端二次过滤，提升准确率。"""
        if angle in ("法人", "信用代码", "电话"):
            low = keyword.strip().lower()
            filtered = []
            for r in records:
                if angle == "法人":
                    name = clean_text(r.get("legalPersonName", "")).lower()
                    if low == name or (name and low in name):
                        filtered.append(r)
                elif angle == "信用代码":
                    code = clean_text(r.get("creditCode", "")).replace(" ", "").lower()
                    if low.replace(" ", "") == code or low in code:
                        filtered.append(r)
                else:
                    if phones_equal(low, r.get("phone", "")):
                        filtered.append(r)
            if filtered:
                return filtered[:limit]
        return records[:limit]