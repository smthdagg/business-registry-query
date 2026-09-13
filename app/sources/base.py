"""数据源协议与统一企业字段定义。

所有数据源把结果规范化为同一种 dict 结构（键为英文常量），
上层服务（搜索/批量/关联/导出）只依赖这一套键，与具体数据源解耦。
"""

from abc import ABC, abstractmethod

# 展示顺序 + 中文标签（详情弹窗 / 导出表头共用）
FIELD_META: list[tuple[str, str]] = [
    ("name", "公司名称"),
    ("legalPersonName", "法定代表人"),
    ("matchType", "命中依据"),
    ("regStatus", "经营状态"),
    ("companyScore", "企业评分"),
    ("creditCode", "统一社会信用代码"),
    ("regNumber", "注册号"),
    ("orgNumber", "组织机构代码"),
    ("companyOrgType", "企业类型"),
    ("regCapital", "注册资本"),
    ("estiblishTime", "成立日期"),
    ("regLocation", "注册地址"),
    ("industry", "所属行业"),
    ("registerInstitute", "登记机关"),
    ("businessScope", "经营范围"),
    ("historyNames", "曾用名"),
    ("companyScale", "企业规模"),
    ("employeesNum", "员工人数"),
    ("phone", "联系电话"),
    ("email", "邮箱"),
    ("website", "官网"),
    ("city", "所在城市"),
    ("district", "所在区县"),
    ("tags", "标签"),
    ("shareholders", "股东"),
    ("investCompanies", "对外投资"),
]

FIELD_LABELS = dict(FIELD_META)

# 关系分析策略说明
STRATEGY_META = {
    "legal": "同法人关联：按法定代表人姓名检索其名下企业",
    "shareholder": "股东关联：解析记录中的自然人股东并检索其名下企业",
    "invest": "对外投资：解析记录中的对外投资企业并逐家收录",
    "phone": "同电话关联：按联系电话检索疑似关联企业",
}

# 查询角度说明
ANGLE_META = {
    "综合": "任意字段模糊匹配",
    "企业名": "按企业名称精确检索",
    "法人": "按法定代表人姓名检索",
    "股东": "按股东姓名检索（需数据源支持）",
    "信用代码": "按统一社会信用代码检索",
    "电话": "按联系电话检索",
}

# 列表页要展示的“重点列”
LIST_COLUMNS = [
    ("name", "公司名称"),
    ("legalPersonName", "法定代表人"),
    ("regStatus", "经营状态"),
    ("regCapital", "注册资本"),
    ("estiblishTime", "成立日期"),
    ("industry", "所属行业"),
    ("phone", "联系电话"),
    ("companyScore", "企业评分"),
]


class SourceError(Exception):
    """数据源错误（网络失败 / 接口拒绝 / 返回异常等）。"""


class BaseSource(ABC):
    # 子类覆盖
    id: str = ""
    label: str = ""
    description: str = ""
    notes: str = ""          # 给用户看的注意事项
    angles: list[str] = []   # 支持的查询角度
    strategies: list[str] = []  # 支持的关联策略
    min_interval: float = 0.0   # 连续两次真实请求的最小间隔（秒）
    cacheable: bool = False

    @abstractmethod
    def search(self, keyword: str, angle: str = "综合", limit: int = 20,
               region_id: str = "") -> list[dict]:
        """按关键词与角度搜索，返回规范化的企业记录列表（按相关度排序）。

        region_id：省级行政区代码（如 340000=安徽省），仅部分源/角度支持。"""
        raise NotImplementedError

    def meta(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "notes": self.notes,
            "angles": self.angles,
            "strategies": self.strategies,
            "minInterval": self.min_interval,
            "cacheable": self.cacheable,
        }
