"""杂项工具函数。"""

import re
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def clean_text(value: Any) -> str:
    """清洗接口返回的文本：去 HTML 标签、合并空白。"""
    if value is None:
        return ""
    text = str(value)
    text = _TAG_RE.sub("", text)
    text = text.replace("\t", " ").replace("\n", " ")
    return _SPACE_RE.sub(" ", text).strip()


def join_list(value: Any, sep: str = "、") -> str:
    """列表字段拼成展示文本，过滤空项。"""
    if isinstance(value, list):
        return sep.join(str(item) for item in value if str(item).strip())
    return clean_text(value)


def digest_phone(phone: Any) -> str:
    """电话只留数字，便于模糊匹配。"""
    return re.sub(r"\D", "", clean_text(phone))


def phones_equal(a: Any, b: Any) -> bool:
    """严格判断两个电话是否相同。

    天眼查返回的电话常被脱敏（如 1892589****），带星号的号码不可信，
    必须完整数字且不含掩码才算同一号码，避免误把不同公司关联为同电话。
    """
    ta, tb = clean_text(a), clean_text(b)
    if not ta or not tb or "*" in ta or "*" in tb:
        return False
    return re.sub(r"\D", "", ta) == re.sub(r"\D", "", tb)


def record_person_hit(rec: dict, person: str) -> bool:
    """判断记录与某自然人是否确有任职/持股关系。

    判断依据（按优先级）：
    0. 人员条目（type=person）：姓名相等即命中（查老板的分组条目）
    1. 企业记录里法定代表人 == 该人
    2. 该人是记录里的自然人股东
    3. 接口给的 matchType 明确标注为“法定代表人/股东/高管/人员匹配”

    仅凭“公司名里含人名”的 公司名称匹配 不构成关联，直接排除。
    """
    p = clean_text(person)
    if not p:
        return False
    if rec.get("type") == "person":
        return clean_text(rec.get("name", "")) == p
    if clean_text(rec.get("legalPersonName", "")) == p:
        return True
    for holder in rec.get("shareholders") or []:
        name = clean_text(holder.get("name", "") if isinstance(holder, dict) else holder)
        if name == p:
            return True
    mt = clean_text(rec.get("matchType", ""))
    return bool(mt) and any(
        token in mt for token in ("法定代表人", "股东", "高管", "法人", "人员")
    )


_CITY_RE = re.compile(r"([\u4e00-\u9fa5]{2,8}?省)?([\u4e00-\u9fa5]{2,8}?市)")
_REGION_RE = re.compile(r"([\u4e00-\u9fa5]{2,8}?(?:自治州|地区|盟))")


def extract_city(address: Any) -> str:
    """从详细地址提取城市：广东省深圳市龙岗区… → 深圳市；北京市朝阳区… → 北京市。"""
    addr = clean_text(address)
    if not addr:
        return ""
    m = _CITY_RE.search(addr)
    if m:
        city = m.group(2) or m.group(1) or ""
    else:
        m = _REGION_RE.search(addr)
        city = m.group(1) if m else ""
    if not city:
        return ""
    # 清洗：去掉自治区/省等前缀残留（如“古自治区锡林郭勒盟”→“锡林郭勒盟”）
    return re.sub(r"^.*?(?:维吾尔|回族|壮族)?自治区", "", city) if "自治区" in city else city


_ENTITY_RE = re.compile(
    r"(公司|企业|中心|合伙|集团|有限|股份|研究院|研究所|事务所|工作室|"
    r"商行|银行|基金|资管|合作社|委员会|学校|医院)"
)


def is_person_name(name: str) -> bool:
    """股东名是否为自然人（非公司实体）；保守判断，宁缺勿滥。"""
    return bool(name) and not _ENTITY_RE.search(name)
