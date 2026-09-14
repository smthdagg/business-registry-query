"""风鸟数据源（riskbird.com，免费注册、接口需登录 Cookie）。

接口族（端点与字段命名参考 ENScan_GO 的 Apache-2.0 实现，Python 代码独立编写）：
- 搜索    POST /riskbird-api/newSearch  -> data.list[]（entid 即真实 PID，无混淆）
- 股东    POST /riskbird-api/companyInfo/list  extractType=shareHolder
- 对外投资 extractType=companyInvest
- 分支    extractType=companyBranch
- 人员企业 POST /riskbird-api/query/person/personEachData
          （orderNo 从人员页 SSR HTML 中提取；返回该人员任法代/高管/股东的全部企业）

响应约定：code=20000 成功；state=limit:tourist 游客配额用尽；limit:auth 登录配额用尽。
"""

import re

import requests

from .. import config
from ..utils import clean_text, extract_city, is_person_name, join_list
from .base import BaseSource, SourceError

_BASE = "https://www.riskbird.com"

_PERSON_EACH = "/riskbird-api/query/person/personEachData"
_ORDERNO_RE = re.compile(r"WEB20\d{12,}")

_ENTITY_RE = re.compile(
    r"(公司|企业|中心|合伙|集团|有限|股份|研究院|研究所|事务所|工作室|"
    r"商行|银行|基金|资管|合作社|委员会|学校|医院)"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,application/xhtml+xml,image/jxr,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "App-Device": "WEB",
    "Content-Type": "application/json",
    "Origin": "https://www.riskbird.com",
    "Referer": "https://www.riskbird.com/ent/",
}

_SEARCH_FIELDS: dict[str, tuple[str, ...]] = {
    "name": ("ENTNAME", "entName", "name"),
    "legalPersonName": ("faren", "personName"),
    "regStatus": ("ENTSTATUS", "entStatus"),
    "phone": ("tels", "telList"),
    "email": ("emails", "emailList"),
    "regCapital": ("regConcat", "recConcat"),
    "estiblishTime": ("esDate",),
    "regLocation": ("dom", "yrAddress"),
    "creditCode": ("UNISCID", "uniscid"),
    "businessScope": ("opScope",),
}


class RiskbirdSource(BaseSource):
    """风鸟：登录 Cookie 驱动的免费源，含股东/对外投资明细。"""

    id = "rb"
    label = "风鸟（需 Cookie）"
    description = "风鸟企业查询平台接口：搜索 + 股东 + 对外投资明细（免费注册账号）。"
    notes = (
        "免费注册 riskbird.com 后，在 .env 配置 RB_COOKIE"
        "（浏览器登录后复制完整 Cookie）后重启使用；游客配额很快用尽。"
    )
    angles = ["综合", "企业名", "法人"]
    strategies = ["shareholder", "invest"]
    min_interval = config.RB_DELAY
    cacheable = True

    def __init__(self) -> None:
        self._bootstrapped = bool(self.init_session(_HEADERS))
        self._last_meta: dict = {}

    def last_meta(self) -> dict:
        return self._last_meta

    def _ensure_session(self) -> None:
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
        self._bootstrapped = bool(self.refresh_cookie())
        kw = clean_text(keyword)
        if not kw:
            return []
        self._ensure_session()
        try:
            return self._search_inner(keyword, angle, limit, region_id)
        except SourceError as e:
            if not config.BROWSER_FALLBACK:
                raise
            # 浏览器降级：仅对风控/登录态类错误有效（网络错误重试也无意义）
            msg = str(e)
            if not any(k in msg for k in ("风控", "登录态", "游客", "非 JSON", "状态异常")):
                raise
            from ..browser import manager
            if not manager.enabled():
                raise
            return self._browser_search(keyword, angle, limit, region_id)

    def _search_inner(self, keyword: str, angle: str, limit: int,
                      region_id: str = "") -> list[dict]:
        kw = clean_text(keyword)
        if not kw:
            return []

        # 法人角度：风鸟“查老板”人员分组（每人独立 personId，可按省筛选），
        # 一次请求覆盖全部同名人员，不再做公司名模糊搜索。
        if angle == "法人":
            return self._search_person_angle(kw, limit, region_id)

        want = max(1, min(int(limit), 1000))
        records: list[dict] = []
        # 综合角度：附带同名人员分组（风鸟“查老板”），人员排前——与网页综合搜索一致
        if angle == "综合":
            try:
                for p in self.person_search(kw, limit=min(limit, 50)):
                    records.append({
                        "name": p["name"], "type": "person", "pid": p["pid"],
                        "count": p["count"], "maxCompany": p.get("maxCompany", ""),
                        "region": (p.get("regions") or [{}])[0].get("name", ""),
                        "matchType": "人员（风鸟）",
                    })
            except SourceError:
                pass

        page = 1
        total = 0
        while page <= 100 and len(records) < want:
            payload = {
                "searchKey": kw,
                "pageNo": str(page),
                # 服务端实际固定 10 条/页，range 参数被忽略；保留以兼容
                "range": "20",
                "referer": "search",
                "queryType": "1",
                "selectConditionData": '{"status":"","sort_field":""}',
            }
            data = self._post("/riskbird-api/newSearch", payload, xs_header=False)
            inner = data.get("data") or {}
            if total == 0:
                total = int(inner.get("totalCount") or 0)
            items = inner.get("list") or []
            if not items:
                break
            for item in items:
                rec = self._normalize(item)
                if rec:
                    records.append(rec)
            if len(records) >= want:
                break
            page += 1
        # 填满条数预算即视为可能还有更多（接口已不返回 totalCount，无法精确判断）
        self._last_meta = {"total": total, "truncated": len(records) >= want}
        return records[:want]

    @staticmethod
    def _normalize(item: dict) -> dict | None:
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
        if not rec.get("city") and rec.get("regLocation"):
            rec["city"] = extract_city(rec["regLocation"])
        pid = item.get("entid") or item.get("entId") or item.get("orderNo")
        if pid:
            rec["_pid"] = clean_text(pid)
        mt = clean_text(item.get("matchType"))
        if mt:
            rec["matchType"] = mt
        tags = []
        for tg in item.get("tags") or item.get("newLabels") or []:
            tg = clean_text(tg)
            if tg:
                tags.append(tg)
        if tags:
            rec["tags"] = tags
        return rec

    def _person_order_no(self, person_id: str) -> str | None:
        """从人员页 SSR HTML 提取会话级 orderNo。"""
        try:
            resp = self._session.get(
                f"{_BASE}/person/{person_id}",
                headers={"Accept": "text/html,application/xhtml+xml,*/*"},
                timeout=config.REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            m = _ORDERNO_RE.search(resp.text)
            return m.group(0) if m else None
        except requests.RequestException:
            return None

    def _post(self, path: str, payload: dict, xs_header: bool = True) -> dict:
        try:
            headers = {"Xs-Content-Type": "application/json"} if xs_header else {}
            resp = self._session.post(
                _BASE + path, json=payload, headers=headers,
                timeout=config.REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise SourceError(f"风鸟网络请求失败：{e}") from None
        try:
            data = resp.json()
        except ValueError:
            snippet = clean_text(resp.text)[:60]
            raise SourceError(
                f"风鸟返回非 JSON（HTTP {resp.status_code}，片段：{snippet!r}）；"
                "多为登录态失效，请更新 RB_COOKIE"
            ) from None
        data_obj = data.get("data") if isinstance(data.get("data"), dict) else {}
        state = data_obj.get("state") or data.get("state")
        if state in ("limit:tourist", "limit:auth"):
            who = "游客" if state == "limit:tourist" else "登录"
            raise SourceError(
                f"风鸟查询配额已用尽（{who}）。请在 .env 配置 RB_COOKIE"
                "（浏览器登录 riskbird.com 后复制完整 Cookie）后重启使用。"
            )
        if resp.status_code in (401, 302):
            raise SourceError("风鸟登录态失效，请更新 RB_COOKIE")
        if data.get("code") != 20000:
            raise SourceError(f"风鸟接口异常：{data.get('msg') or data.get('code')}")
        return data

    def _get(self, path: str, params: dict) -> dict | None:
        try:
            resp = self._session.get(
                _BASE + path, params=params,
                headers={"Xs-Content-Type": "application/json"},
                timeout=config.REQUEST_TIMEOUT,
            )
            return resp.json()
        except (requests.RequestException, ValueError):
            return None

    def _list_api(self, order_no: str, extract_type: str) -> list[dict]:
        payload = {
            "filterCnd": "1",
            "page": "1",
            "size": "100",
            "orderNo": order_no,
            "extractType": extract_type,
            "sortField": "",
        }
        if extract_type == "companyInvest":
            payload.update({"category": "-100", "percentLevel": "-100", "province": "-100"})
        data = self._post("/riskbird-api/companyInfo/list", payload)
        return (data.get("data") or {}).get("apiData") or []

    def enrich(self, rec: dict) -> dict:
        """补全股东/对外投资明细与法人 personId；失败静默返回原记录。"""
        pid = rec.get("_pid")
        if not pid or "investCompanies" in rec:
            return rec
        detail = self._get("/api/ent/query", {"entId": pid}) or {}
        order_no = clean_text(detail.get("orderNo"))
        jb = (((detail or {}).get("basicResult") or {}).get("apiData") or {}).get("list") or {}
        jbxx = jb.get("jbxxInfo") or {}
        if jbxx.get("personId"):
            rec["legalPersonId"] = clean_text(jbxx["personId"])
        if not order_no:
            return rec
        try:
            holders_raw = self._list_api(order_no, "shareHolder")
        except SourceError:
            holders_raw = []
        try:
            invest_raw = self._list_api(order_no, "companyInvest")
        except SourceError:
            invest_raw = []

        holders = []
        for it in holders_raw:
            name = clean_text(it.get("shaName") or it.get("name"))
            if not name or not is_person_name(name):
                continue
            holders.append({
                "name": name,
                "percent": clean_text(it.get("fundedRatio")),
                "pid": clean_text(it.get("shaId")),
            })
        if holders:
            rec["shareholders"] = holders

        invest = [clean_text(it.get("entName")) for it in invest_raw]
        invest = [x for x in invest if x]
        if invest:
            rec["investCompanies"] = invest
        return rec

    def _browser_fetch_api(self, path: str, payload: dict) -> dict:
        """经浏览器页面上下文调用风鸟 API（自动带登录态/指纹；需 Camoufox）。"""
        from ..browser import manager
        result = manager.fetch_api("rb", path, payload)
        if isinstance(result, dict) and result.get("state") in ("limit:tourist", "limit:auth"):
            raise SourceError(f"风鸟配额限制：{result.get('state')}")
        code = result.get("code")
        if code not in (None, 20000):
            raise SourceError(f"风鸟接口异常：{result.get('msg') or code}")
        return result

    def _browser_person_groups(self, per_name: str, limit: int) -> list[dict]:
        data = self._browser_fetch_api("/riskbird-api/api/v1/persons/search", {
            "type": "search", "perName": per_name,
            "pageNo": 1, "range": max(1, min(int(limit), 50)),
            "regionId": "", "nicId": "",
        })
        out = []
        for it in (data.get("data") or {}).get("list") or []:
            pid = clean_text(it.get("personidStr"))
            name = clean_text(it.get("per_name") or it.get("perName"))
            if not pid or not name:
                continue
            out.append({"name": name, "pid": pid,
                        "count": int(it.get("ent_count") or 0),
                        "maxCompany": clean_text(it.get("max_regcap_entname"))})
        return out

    def _browser_search(self, keyword: str, angle: str, limit: int,
                        region_id: str) -> list[dict]:
        """浏览器通道搜索（HTTP 被风控时的降级路径）。"""
        records: list[dict] = []
        if angle == "法人":
            for g in self._browser_person_groups(keyword, limit):
                records.append({
                    "name": g["name"], "type": "person", "pid": g["pid"],
                    "count": g["count"], "maxCompany": g["maxCompany"],
                    "matchType": "人员（风鸟·浏览器）",
                })
            return records[:limit]
        page = 1
        want = max(1, min(int(limit), 100))
        while page <= 6 and len(records) < want:
            data = self._browser_fetch_api("/riskbird-api/newSearch", {
                "searchKey": keyword, "pageNo": str(page), "range": "20",
                "referer": "search", "queryType": "1",
                "selectConditionData": '{"status":"","sort_field":""}',
            }, xs_header=False)
            items = (data.get("data") or {}).get("list") or []
            if not items:
                break
            for item in items:
                rec = self._normalize(item)
                if rec:
                    records.append(rec)
            if len(records) >= want:
                break
            page += 1
        return records[:want]

    def _search_person_angle(self, per_name: str, limit: int,
                             region_id: str = "") -> list[dict]:
        """法人角度：返回“人员条目”（每人独立 personId，点选查任职企业）。"""
        out: list[dict] = []
        try:
            persons = self.person_search(per_name, limit=min(limit, 50),
                                         region_id=region_id)
        except SourceError:
            persons = []
        for p in persons:
            out.append({
                "name": p["name"],
                "type": "person",
                "pid": p["pid"],
                "count": p["count"],
                "maxCompany": p.get("maxCompany", ""),
                "region": (p.get("regions") or [{}])[0].get("name", ""),
                "matchType": "人员（风鸟）",
            })
        return out

    # ---------------- 人员搜索（同名人员分组，风鸟网页同款） ----------------

    def person_search(self, per_name: str, limit: int = 18,
                      region_id: str = "", nic_id: str = "") -> list[dict]:
        """按姓名搜索人员：同名人员各自独立成条（personidStr 精确区分），带任职企业数。

        region_id：省级行政区代码（如 340000=安徽省），服务端按人员任职企业所在省筛选。
        """
        try:
            resp = self._session.post(
                f"{_BASE}/riskbird-api/api/v1/persons/search",
                json={"type": "search", "perName": clean_text(per_name),
                      "pageNo": 1, "range": max(1, min(int(limit), 50)),
                      "regionId": clean_text(region_id), "nicId": clean_text(nic_id)},
                headers={"Accept": "application/json"},
                timeout=config.REQUEST_TIMEOUT,
            )
            data = resp.json()
        except requests.RequestException as e:
            raise SourceError(f"风鸟人员搜索失败：{e}") from None
        except ValueError:
            raise SourceError("风鸟人员搜索返回非 JSON（请更新 RB_COOKIE）") from None
        inner = data.get("data") or {}
        out = []
        for it in inner.get("list") or []:
            pid = clean_text(it.get("personidStr"))
            name = clean_text(it.get("per_name") or it.get("perName"))
            if not pid or not name:
                continue
            # 地区分布统计（personMohuEntInfo.entList 的 region_id_cn）
            mohu = it.get("personMohuEntInfo") or {}
            ent_list = mohu.get("entList") or []
            region_count: dict[str, int] = {}
            for e in ent_list:
                if not isinstance(e, dict):
                    continue
                rn = clean_text(e.get("region_id_cn") or e.get("regionIdCn"))
                if rn:
                    region_count[rn] = region_count.get(rn, 0) + 1
            regions = [{"name": k, "count": v}
                       for k, v in sorted(region_count.items(), key=lambda x: -x[1])][:6]
            # 合作伙伴（前 4 位）
            partners = []
            for pt in (it.get("partner_info") or [])[:4]:
                pn = clean_text(pt.get("perName"))
                if pn:
                    partners.append({
                        "name": pn,
                        "count": int(pt.get("count") or 0),
                        "company": clean_text(pt.get("entName")),
                    })
            out.append({
                "name": name,
                "pid": pid,
                "count": int(it.get("ent_count") or 0),
                "maxCompany": clean_text(it.get("max_regcap_entname")),
                "regions": regions,
                "partners": partners,
            })
        return out

    # ---------------- 人员 → 关联企业（风鸟网页同款，精确 + 带职务） ----------------

    def _post_form(self, path: str, data: dict) -> dict:
        """表单 POST（personEachData 不接受纯 application/json）。"""
        try:
            resp = self._session.post(
                _BASE + path, data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=config.REQUEST_TIMEOUT,
            )
            return resp.json()
        except requests.RequestException as e:
            raise SourceError(f"风鸟网络请求失败：{e}") from None
        except ValueError:
            raise SourceError("风鸟返回非 JSON（登录态失效，请更新 RB_COOKIE）") from None

    def legal_person_pid(self, company_name: str) -> str | None:
        """按企业名查现任法定代表人的 personId（查不到返回 None）。

        用于同名人员复核：天眼查按姓名匹配的法人结果必须对照
        风鸟 personId，排除同名不同人。"""
        rows = self.search(company_name, "企业名", limit=3)
        hit = next((r for r in rows if (r.get("name") or "") == company_name), None)
        if not hit or not hit.get("_pid"):
            return None
        detail = self._get("/api/ent/query", {"entId": hit["_pid"]}) or {}
        jb = (((detail.get("basicResult") or {}).get("apiData") or {}).get("list") or {}).get("jbxxInfo") or {}
        return clean_text(jb.get("personId")) or None

    def person_companies(self, person_id: str, limit: int = 20) -> list[dict]:
        """该人员任法代/高管/股东的全部企业（风鸟口径，含职务）。"""
        order_no = self._person_order_no(person_id)
        if not order_no:
            raise SourceError("风鸟人员页未取得 orderNo（可能 Cookie 失效）")
        payload = {
            "pageNo": 1, "range": max(1, min(int(limit), 50)),
            "orderNo": order_no, "dataType": "all",
            "conpropType": "", "entstatus": "", "regionId": "", "nicId": "",
        }
        data = self._post_form(_PERSON_EACH, payload)
        items = (data.get("data") or {}).get("list") or []
        out = []
        for it in items:
            name = clean_text(it.get("entname"))
            if not name:
                continue
            out.append({
                "name": name,
                "legalPersonName": clean_text(it.get("legalPersonName")),
                "regStatus": clean_text(it.get("entstatusCn")),
                "regCapital": clean_text(it.get("regcapCn")),
                "estiblishTime": clean_text(it.get("esdateStr")),
                "industry": clean_text(it.get("nicIdCn")),
                "role": clean_text(it.get("identity")),
                "position": clean_text(it.get("positionCn")),
                "source": "rb",
                "_pid": clean_text(it.get("entidstr")),
            })
        return out
