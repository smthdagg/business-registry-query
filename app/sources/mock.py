"""内置演示数据源：一组精心构造、互相勾连的“虚拟企业族谱”。

用途：
- 本地无网络 / 无天眼查 Cookie 时，完整体验全部功能；
- 单元测试的数据基础（确定性输出）；
- 关联图谱的完整效果演示（同法人 / 股东 / 对外投资 / 同电话）。

场景设定（纯虚构）：
  「星辰控股」集团下辖置业 / 科技 / 供应链，科技又投了数据服务；
  供应链与「远航」系（物流、仓储）通过自然人刘洋、王强互有交叉；
  远港实业与星辰置业共用同一电话（疑似关联演示）；
  恒信贸易、江南投资等用作对照样本。
"""

import copy
from typing import Any

from ..utils import clean_text, digest_phone
from .base import BaseSource, SourceError

# ---------------------------------------------------------------------------
# 虚拟企业库
# ---------------------------------------------------------------------------

_P = "上海市市场监督管理局"


def _mk(
    name: str,
    legal: str,
    holders: list[tuple[str, str]],
    invest: list[str],
    phone: str,
    city: str,
    district: str,
    address: str,
    authority: str,
    credit: str,
    industry: str = "商务服务业",
    capital: str = "5000万人民币",
    established: str = "2016-01-01",
    status: str = "存续",
    org_type: str = "有限责任公司(自然人投资或控股)",
    scope: str = "一般项目：企业管理；投资管理；信息咨询服务（不含许可类信息咨询服务）。",
    history: list[str] | None = None,
    tags: list[str] | None = None,
    score: int = 80,
    scale: str = "中型",
    employees: str = "100-199人",
    reg_number: str = "",
    org_number: str = "",
    email: str = "",
    website: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "legalPersonName": legal,
        "regStatus": status,
        "companyScore": score,
        "creditCode": credit,
        "regNumber": reg_number,
        "orgNumber": org_number,
        "companyOrgType": org_type,
        "regCapital": capital,
        "estiblishTime": established,
        "regLocation": address,
        "industry": industry,
        "registerInstitute": authority,
        "businessScope": scope,
        "historyNames": history or [],
        "companyScale": scale,
        "employeesNum": employees,
        "phone": phone,
        "email": email,
        "website": website,
        "city": city,
        "district": district,
        "tags": tags or [],
        # 结构化字段：股东 [(姓名, 比例)]；对外投资 [企业名]
        "shareholders": [{"name": n, "percent": p} for n, p in holders],
        "investCompanies": invest,
    }


_UNIVERSE: list[dict[str, Any]] = [
    _mk(
        name="星辰控股集团有限公司", legal="张伟明",
        holders=[("张伟明", "60%"), ("张丽", "40%")],
        invest=["星辰置业有限公司", "星辰科技有限公司", "星辰供应链管理有限公司"],
        phone="021-60001001", city="上海市", district="浦东新区",
        address="上海市浦东新区张江路1号", authority=_P,
        credit="913100006811234567", industry="商务服务业",
        capital="20000万人民币", established="2008-06-18", status="存续",
        org_type="有限责任公司(自然人投资或控股)", score=96, scale="大型",
        employees="1000人以上", history=["星辰控股有限公司"],
        tags=["中国民营企业500强"],
        scope="一般项目：以自有资金从事投资活动；企业管理咨询；实业投资；创业投资；货物进出口。",
        email="ir@xingchen-holdings.cn", website="https://www.xingchen-holdings.example.cn",
        reg_number="310115000234567", org_number="68112345-6",
    ),
    _mk(
        name="星辰置业有限公司", legal="张伟明",
        holders=[("张伟明", "85%"), ("陈静", "15%")],
        invest=[], phone="021-60001002", city="上海市", district="浦东新区",
        address="上海市浦东新区张江路88号", authority=_P,
        credit="913101156811234568", industry="房地产业",
        capital="10000万人民币", established="2012-03-09", status="存续",
        org_type="有限责任公司(自然人投资或控股)", score=88, scale="中型",
        employees="200-499人", history=["上海星辰置业发展有限公司"],
        scope="房地产开发经营；物业管理；自有房屋租赁；室内装饰装修工程。",
        email="service@xingchen-zhiye.example.cn",
        reg_number="310115001234568", org_number="68112345-7",
    ),
    _mk(
        name="星辰科技有限公司", legal="李建国",
        holders=[("李建国", "45%"), ("陈静", "30%"), ("王强", "25%")],
        invest=["星辰数据服务有限公司"],
        phone="021-60001003", city="上海市", district="浦东新区",
        address="上海市浦东新区张江路2号", authority=_P,
        credit="913101156811234569", industry="软件和信息技术服务业",
        capital="5000万人民币", established="2015-07-22", status="存续",
        org_type="有限责任公司(自然人投资或控股)", score=92, scale="中型",
        employees="500-999人", tags=["高新技术企业", "科技型中小企业"],
        scope="软件开发；信息系统集成服务；数据处理和存储支持服务；人工智能应用软件开发。",
        website="https://www.xingchen-tech.example.cn",
        reg_number="310115001234569", org_number="68112345-8",
    ),
    _mk(
        name="星辰供应链管理有限公司", legal="刘洋",
        holders=[("刘洋", "55%"), ("陈静", "45%")],
        invest=["远航仓储有限公司"],
        phone="021-60001004", city="上海市", district="青浦区",
        address="上海市青浦区华徐公路888号", authority=_P,
        credit="913101186811234570", industry="多式联运和运输代理业",
        capital="3000万人民币", established="2016-11-02", status="在业",
        org_type="有限责任公司(自然人投资或控股)", score=84, scale="小型",
        employees="100-199人", tags=["AAAA级物流企业"],
        scope="供应链管理服务；国内货物运输代理；仓储服务（除危险化学品）；装卸搬运。",
        reg_number="310118001234570", org_number="68112345-9",
    ),
    _mk(
        name="星辰数据服务有限公司", legal="李建国",
        holders=[("李建国", "70%"), ("王强", "30%")],
        invest=[], phone="021-60001005", city="上海市", district="浦东新区",
        address="上海市浦东新区张江路2号3幢5层", authority=_P,
        credit="913101156811234571", industry="互联网和相关服务",
        capital="1000万人民币", established="2018-09-15", status="存续",
        org_type="有限责任公司(自然人投资或控股)", score=78, scale="小型",
        employees="50-99人", tags=["高新技术企业"],
        scope="互联网数据服务；信息咨询服务；计算机系统服务；网络技术服务。",
        reg_number="310115001234571", org_number="68112345-0",
    ),
    _mk(
        name="远航物流有限公司", legal="刘洋",
        holders=[("刘洋", "60%"), ("王强", "40%")],
        invest=["远航仓储有限公司"],
        phone="010-88000001", city="北京市", district="朝阳区",
        address="北京市朝阳区东四环中路188号", authority="北京市市场监督管理局",
        credit="911100006811234572", industry="道路运输业",
        capital="8000万人民币", established="2010-04-20", status="存续",
        org_type="有限责任公司(自然人投资或控股)", score=86, scale="中型",
        employees="500-999人", tags=["国家5A级物流企业"],
        scope="普通货运；货物专用运输（集装箱）；仓储服务；货物进出口、技术进出口、代理进出口。",
        email="ops@yuanhang-logistics.example.cn",
        reg_number="110105001234572", org_number="68112345-1",
    ),
    _mk(
        name="远航仓储有限公司", legal="王强",
        holders=[("王强", "80%"), ("刘洋", "20%")],
        invest=[], phone="010-88000002", city="北京市", district="通州区",
        address="北京市通州区物流产业园区16号", authority="北京市市场监督管理局",
        credit="911100006811234573", industry="装卸搬运和仓储业",
        capital="2000万人民币", established="2013-12-05", status="存续",
        org_type="有限责任公司(自然人投资或控股)", score=80, scale="小型",
        employees="100-199人",
        scope="普通货物仓储服务；低温仓储；装卸搬运；仓储设备租赁服务。",
        reg_number="110112001234573", org_number="68112345-2",
    ),
    _mk(
        name="恒信贸易有限公司", legal="陈静",
        holders=[("陈静", "100%")],
        invest=[], phone="0755-88000003", city="深圳市", district="福田区",
        address="深圳市福田区华强北路科技园3栋", authority="深圳市市场监督管理局",
        credit="914403006811234574", industry="批发业",
        capital="500万人民币", established="2017-05-30", status="注销",
        org_type="有限责任公司(自然人独资)", score=72, scale="小型",
        employees="50-99人", tags=["一般纳税人"],
        scope="电子元器件、计算机软硬件的购销；国内贸易；经营进出口业务。",
        reg_number="440306001234574", org_number="68112345-3",
    ),
    _mk(
        name="远港实业有限公司", legal="赵大川",
        holders=[("赵大川", "100%")],
        invest=[], phone="021-60001002", city="上海市", district="浦东新区",
        address="上海市浦东新区川沙路99号", authority=_P,
        credit="913101156811234575", industry="通用设备制造业",
        capital="2000万人民币", established="2019-01-11", status="存续",
        org_type="有限责任公司(自然人独资)", score=65, scale="小型",
        employees="100-199人",
        scope="通用机械设备制造；金属制品加工；五金交电、机电设备销售。",
        reg_number="310115001234575", org_number="68112345-4",
    ),
]

_UNIVERSE_LOWER = [dict(c) for c in _UNIVERSE]


class MockSource(BaseSource):
    """内置演示数据源。"""

    id = "mock"
    label = "演示数据（Mock）"
    description = "内置虚构企业数据，用于本地体验全部功能与测试，无需联网。"
    notes = "数据为虚构样本，仅演示功能，不代表真实工商信息。"
    angles = ["综合", "企业名", "法人", "股东", "信用代码", "电话"]
    strategies = ["legal", "shareholder", "invest", "phone"]
    min_interval = 0.0
    cacheable = False

    def search(self, keyword: str, angle: str = "综合", limit: int = 20,
               region_id: str = "") -> list[dict]:
        kw = clean_text(keyword)
        if not kw:
            return []
        lower = kw.lower()

        def score(c: dict) -> int:
            # 名称越靠前的命中越相关
            name = c["name"]
            if name.lower() == lower:
                return 300
            if name.startswith(lower) or lower.startswith(name[: max(1, len(lower))]):
                return 200
            return 100

        hits: list[dict] = []
        for c in _UNIVERSE_LOWER:
            ok = self._match(c, angle, kw, lower)
            if ok:
                hits.append(c)
        hits.sort(key=lambda c: (-score(c), -(c.get("companyScore") or 0)))
        return copy.deepcopy(hits[:limit])

    def _match(self, c: dict, angle: str, kw: str, lower: str) -> bool:
        name = c["name"]
        legal = c.get("legalPersonName") or ""
        holders = "、".join(h["name"] for h in c.get("shareholders") or [])
        if angle == "综合":
            hay = " ".join(
                [name, legal, holders, c.get("creditCode", ""), c.get("phone", ""),
                 c.get("regNumber", ""), c.get("industry", "")]
            ).lower()
            return kw in hay
        if angle == "企业名":
            return lower in name.lower()
        if angle == "法人":
            return lower in legal.lower() or legal == kw
        if angle == "股东":
            return kw in holders
        if angle == "信用代码":
            code = "".join(clean_text(c.get("creditCode", "")).split()).lower()
            return lower in code or code == lower
        if angle == "电话":
            return len(kw) >= 3 and digest_phone(kw) in digest_phone(c.get("phone", ""))
        raise SourceError(f"演示数据源不支持查询角度：{angle}")


def build() -> MockSource:
    return MockSource()
