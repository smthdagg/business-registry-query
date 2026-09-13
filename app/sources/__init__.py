"""数据源注册表。"""

from .. import config, db
from .base import BaseSource, SourceError, FIELD_LABELS, FIELD_META, LIST_COLUMNS  # noqa: F401
from .aiqicha import AiqichaSource
from .cha88 import Cha88Source
from .mock import MockSource
from .riskbird import RiskbirdSource
from .tianyancha import TianyanchaSource

_SOURCES: dict[str, BaseSource] = {
    "mock": MockSource(),
    "tyc": TianyanchaSource(),
    "aqc": AiqichaSource(),
    "rb": RiskbirdSource(),
    "c88": Cha88Source(),
}

# 虚拟聚合源（不是 BaseSource，由 services.aggregate 路由）
ALL_ID = "all"
_AGG_META = {
    "id": ALL_ID,
    "label": "聚合查询（全部源）",
    "description": "一次并行查询 88查/天眼查/风鸟/爱企查，结果合并去重并标注来源。",
    "notes": "未配置 Cookie 的源会自动跳过并在结果中提示失败原因。",
    "angles": ["综合", "企业名", "法人", "信用代码", "电话", "股东"],
    "strategies": ["legal", "shareholder", "invest", "phone"],
    "minInterval": 0,
    "cacheable": True,
}


def list_sources() -> list[dict]:
    metas = [s.meta() for s in _SOURCES.values()]
    metas.append(dict(_AGG_META))
    return metas


def get_source(source_id: str | None) -> BaseSource:
    sid = source_id or db.get_config("source") or config.DEFAULT_SOURCE
    if sid == ALL_ID:
        raise SourceError("聚合源不是具体数据源，请走 services.aggregate")
    source = _SOURCES.get(sid)
    if source is None:
        raise SourceError(f"未知数据源：{sid}")
    return source


def set_source(source_id: str) -> None:
    if source_id != ALL_ID and source_id not in _SOURCES:
        raise SourceError(f"未知数据源：{source_id}")
    db.set_config("source", source_id)
