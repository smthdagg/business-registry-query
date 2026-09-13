"""公司档案（Profile）：对齐天眼查/风鸟的数据架构。

一次取全一家公司的结构化档案，而不是搜索接口里的浅表字段：

- 基本信息：以风鸟 jbxxInfo（60+ 字段）为主，其它数据源并行比对补充
  （88查的电话/官网/标签、天眼查的命中信息等），每个字段记 sources；
- 股东：shareHolder 明细（自然人/实体分类 + 持股比例）；
- 对外投资：companyInvest 明细（子公司/被投企业 + 比例）；
- 分支机构：companyBranch 明细；
- 主要人员：法定代表人 + 自然人股东。

数据源策略：风鸟（SVIP）负责结构化明细；聚合搜索负责跨源比对补充。
任何区块都标注来源，取不到时给明确的缺失原因。
"""

from .. import db, sources
from ..sources.base import SourceError
from ..utils import clean_text, is_person_name, join_list
from . import aggregate
from .pacing import wait_interval

# ---------------- 本地规范化比对 ----------------

def normalize_capital(v: str) -> str:
    """注册资本统一为 X万元 / X亿（源内格式不一：30000万 / 4114113.182 / 20000万元人民币）。"""
    v = clean_text(v)
    if not v:
        return ""
    v = v.replace("人民币", "").replace("，", ",").strip()
    num = v.replace(",", "").replace(" ", "")
    if num.replace(".", "").isdigit():
        if num.endswith("元"):
            num = num[:-1]
        return num + "万元"
    if v.endswith("万"):
        return v[:-1] + "万元"
    return v


def normalize_date(v: str) -> str:
    """成立日期统一 YYYY-MM-DD。"""
    v = clean_text(v).replace("/", "-").replace(".", "-")
    if len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return v[:10]
    return v


def normalize_phone(v) -> str:
    """多值电话去重并保持顺序。"""
    if isinstance(v, list):
        items = [clean_text(x) for x in v]
    else:
        items = [x.strip() for x in clean_text(v).replace(";", "、").split("、")]
    seen = []
    for x in items:
        if x and x not in seen:
            seen.append(x)
    return "、".join(seen)


def normalize_basic(basic: dict) -> dict:
    out = dict(basic)
    if out.get("regCapital"):
        out["regCapital"] = normalize_capital(out["regCapital"])
    if out.get("estiblishTime"):
        out["estiblishTime"] = normalize_date(out["estiblishTime"])
    for key in ("phone", "email"):
        if out.get(key):
            out[key] = normalize_phone(out[key])
    return out

_RB_BASIC_FIELDS: dict[str, tuple[str, ...]] = {
    "name": ("entName",),
    "legalPersonName": ("personName", "legalPersonName"),
    "regStatus": ("entStatus",),
    "creditCode": ("uniscid", "creditCode"),
    "regCapital": ("regCapital", "regCap", "regCapitalNum"),
    "estiblishTime": ("esDate",),
    "regLocation": ("yrAddress", "dom", "address"),
    "businessScope": ("opScope", "businessScope"),
    "companyOrgType": ("entType", "entTypeWord"),
    "regNumber": ("regNo", "regNumber", "licenseNumber"),
    "phone": ("telList", "telephone", "phone"),
    "email": ("emailList", "email"),
    "historyNames": ("historyNames", "oldEntName"),
    "industry": ("industry", "industryWord", "categoryStr"),
    "registerInstitute": ("regInstitute", "registerInstitute", "authority"),
    "companyScale": ("companyScale",),
    "employeesNum": ("staffNum", "employeesNum", "employees"),
    "city": ("city",),
    "district": ("district",),
}


def _rb_basic_from_jbxx(jb: dict) -> dict:
    basic: dict = {}
    for key, candidates in _RB_BASIC_FIELDS.items():
        for field in candidates:
            value = jb.get(field)
            if isinstance(value, list):
                value = join_list(value)
            else:
                value = clean_text(value)
            if not value:
                continue
            # 风鸟的注册资本是纯数字（单位万元），补上单位
            if key == "regCapital" and value.replace(".", "").replace(",", "").isdigit():
                value += "万"
            basic[key] = value
            break
    return basic


def _rb_shareholders(items: list[dict]) -> list[dict]:
    out = []
    for it in items or []:
        name = clean_text(it.get("shaName"))
        if not name:
            continue
        out.append({
            "name": name,
            "percent": clean_text(it.get("fundedRatio")),
            "subscribed": clean_text(it.get("subConAm")),
            "type": "person" if is_person_name(name) else "entity",
            "pid": clean_text(it.get("shaId")),
        })
    return out


def _rb_invest(items: list[dict]) -> list[dict]:
    out = []
    for it in items or []:
        name = clean_text(it.get("entName"))
        if not name:
            continue
        out.append({
            "name": name,
            "percent": clean_text(it.get("funderRatio")),
            "status": clean_text(it.get("entStatus")),
            "pid": clean_text(it.get("entid")),
        })
    return out


def _rb_branches(items: list[dict]) -> list[dict]:
    out = []
    for it in items or []:
        name = clean_text(it.get("brName"))
        if not name:
            continue
        out.append({
            "name": name,
            "principal": clean_text(it.get("brPrincipal")),
            "status": clean_text(it.get("entStatus")),
            "pid": clean_text(it.get("entid")),
        })
    return out


def _fill_executive_roles(profile: dict) -> None:
    """高管职务反查：风鸟 personEachData 返回该人员任法代/高管/股东的逐企业职务，
    从中挑出与本公司匹配的职务，补全主要人员角色（如“董事长、董事”）。"""
    if profile.get("source") != "rb":
        return
    if not any(p.get("pid") for p in profile.get("persons") or []):
        return
    try:
        rb = sources.get_source("rb")
    except SourceError:
        return
    low = profile.get("matchName", "").lower()
    for person in (profile.get("persons") or [])[:6]:
        if not person.get("pid"):
            continue
        try:
            wait_interval(rb)
            companies = rb.person_companies(person["pid"], limit=20)
        except Exception:
            continue
        hit = next((c for c in companies if (c.get("name") or "").lower() == low), None)
        if hit and hit.get("role"):
            person["role"] = hit["role"]
            if hit.get("position"):
                person["position"] = hit["position"]


def build_profile(record: dict | None = None, name: str | None = None,
                  source_id: str | None = None) -> dict:
    """构建公司档案。record/name 至少给一个；source_id 指定结构化明细源（默认 rb）。"""
    kw = (name or (record or {}).get("name") or "").strip()
    if not kw:
        raise ValueError("公司名不能为空")

    profile = {
        "basic": {}, "basicSources": {},
        "shareholders": [], "invest": [], "branches": [],
        "persons": [], "source": None, "sourceError": None,
        "crossDetail": [], "matchName": kw,
    }

    # 1) 定位目标企业：优先用调用方给的记录，否则到指定/候选源里精确检索
    root = dict(record) if record else None
    detail_src = None
    if source_id in (None, "all"):
        candidates = aggregate.REAL_ORDER
    else:
        candidates = [source_id]

    if root is None:
        low = kw.lower()
        for cand in candidates:
            try:
                src = sources.get_source(cand)
            except SourceError:
                continue
            try:
                wait_interval(src)
                rows = src.search(kw, "企业名", limit=10)
            except SourceError as e:
                if profile["sourceError"] is None:
                    profile["sourceError"] = f"{cand}: {e}"
                continue
            exact = [r for r in rows if (r.get("name") or "").lower() == low]
            if not exact:
                continue  # 只采用精确匹配，避免锚定到同名/近似企业
            root = exact[0]
            detail_src = src
            break

    # 2) 风鸟结构化明细（jbxxInfo + 股东/投资/分支）
    if root is not None:
        root["name"] = root.get("name") or kw
        src_tags = list(root.get("sources") or []) or [root.get("source") or "search"]
        for k, v in root.items():
            if k not in (None, "") and v not in (None, "", []):
                profile["basic"].setdefault(k, v)
                profile["basicSources"].setdefault(k, list(src_tags))
        rb_pid = root.get("_pid") if (
            (detail_src and detail_src.id == "rb")
            or root.get("source") == "rb"
        ) else None
        if not rb_pid:
            # 根不是风鸟给的：到风鸟里精确找一次，拿 entid
            try:
                rb = sources.get_source("rb")
                wait_interval(rb)
                rows = rb.search(kw, "企业名", limit=5)
                exact = [r for r in rows if (r.get("name") or "").lower() == kw.lower()]
                if exact:
                    rb_rec = exact[0]
                    rb_rec["source"] = "rb"
                    rb_pid = rb_rec.get("_pid")
                    for k, v in rb_rec.items():
                        if k not in ("_pid", "source") and v not in (None, "", []):
                            if not profile["basic"].get(k):
                                profile["basic"][k] = v
                                profile["basicSources"][k] = ["rb"]
            except SourceError as e:
                profile["sourceError"] = f"rb: {e}"

        if rb_pid:
            profile["source"] = "rb"
            rb = sources.get_source("rb")
            wait_interval(rb)
            detail = rb._get("/api/ent/query", {"entId": rb_pid}) or {}
            order_no = clean_text(detail.get("orderNo"))
            jb = (((detail.get("basicResult") or {}).get("apiData") or {}).get("list") or {}).get("jbxxInfo") or {}
            if jb:
                rb_basic = _rb_basic_from_jbxx(jb)
                for k, v in rb_basic.items():
                    if v not in (None, "", []):
                        profile["basic"][k] = v
                        profile["basicSources"][k] = ["rb"]
                profile["legalPersonId"] = clean_text(jb.get("personId"))
            if order_no:
                wait_interval(rb)
                try:
                    profile["shareholders"] = _rb_shareholders(rb._list_api(order_no, "shareHolder"))
                except SourceError as e:
                    profile["sourceError"] = f"rb 股东: {e}"
                wait_interval(rb)
                try:
                    profile["invest"] = _rb_invest(rb._list_api(order_no, "companyInvest"))
                except SourceError as e:
                    profile["sourceError"] = profile["sourceError"] or f"rb 投资: {e}"
                wait_interval(rb)
                try:
                    profile["branches"] = _rb_branches(rb._list_api(order_no, "companyBranch"))
                except SourceError as e:
                    profile["sourceError"] = profile["sourceError"] or f"rb 分支: {e}"
        # 主要人员 = 法人 + 自然人股东；再通过人员接口反查该公司内职务（高管）
        persons = []
        legal = clean_text(profile["basic"].get("legalPersonName"))
        if legal:
            persons.append({"name": legal, "role": "法定代表人",
                            "pid": profile.get("legalPersonId")})
        for h in profile["shareholders"]:
            if h["type"] == "person" and h["name"] != legal:
                persons.append({"name": h["name"],
                                "role": f"股东{('（' + h['percent'] + '）') if h['percent'] else ''}",
                                "pid": h.get("pid")})
        profile["persons"] = persons
        _fill_executive_roles(profile)

    if root is None:
        raise ValueError(f"未检索到企业【{kw}】，无法生成档案。")
    profile["basic"].setdefault("name", root.get("name") or kw)
    profile["matchName"] = root.get("name") or kw

    # 3) 跨源比对补充：聚合搜索一次，用其它源的字段补 basic 的空格
    try:
        agg = aggregate.do_search(None, "企业名", profile["matchName"], limit=10, history=False)
        low = profile["matchName"].lower()
        exact = next((r for r in agg.get("results") or []
                      if (r.get("name") or "").lower() == low), None)
        if exact:
            for k, v in exact.items():
                if k in ("sources", "source", "_pid") or v in (None, "", []):
                    continue
                if not profile["basic"].get(k):
                    profile["basic"][k] = v
                    profile["basicSources"][k] = list(exact.get("sources") or [])
                elif profile["basicSources"].get(k) and set(exact.get("sources") or []) - set(profile["basicSources"][k]):
                    profile["basicSources"][k] = sorted(set(profile["basicSources"][k]) | set(exact.get("sources") or []))
            profile["crossDetail"] = agg.get("sourcesDetail") or []
    except Exception:
        pass

    profile["basic"] = normalize_basic(profile["basic"])

    # 去重：对外投资 / 分支机构 按企业名（保留首个）
    for key in ("invest", "branches"):
        seen_names = set()
        dedup = []
        for item in profile[key]:
            if item["name"] in seen_names:
                continue
            seen_names.add(item["name"])
            dedup.append(item)
        profile[key] = dedup

    db.add_history("profile", "档案", profile["matchName"], True,
                   len(profile["shareholders"]) + len(profile["invest"]) + len(profile["branches"]))
    return profile
