"""单次搜索：数据源路由 + 实时源缓存 + 查询历史 + 连通性探测。"""

import time

from .. import db, sources
from ..sources.base import SourceError
from ..utils import record_person_hit
from .pacing import wait_interval

DEFAULT_LIMIT = 20


def do_search(
    source_id: str,
    angle: str,
    keyword: str,
    limit: int = DEFAULT_LIMIT,
    cache: bool = True,
    history: bool = True,
    person: str | None = None,
    region_id: str = "",
) -> dict:
    """执行一次搜索，返回统一结果结构（不抛业务异常）。

    person 非空时按“人名检索”处理：结果过滤为与该自然人确有
    任职/持股关系的企业（record_person_hit），供人员弹窗使用。
    """
    source = sources.get_source(source_id)
    angle_used = angle if angle in source.angles else "综合"
    kw = keyword.strip()

    if not kw:
        return {"ok": False, "error": "关键词不能为空", "results": []}

    cache_key = f"{source.id}|{angle_used}|{kw}|p:{person or ''}|r:{region_id}|l:{min(limit, 1000)}"
    cached: list | None = None
    if cache and source.cacheable:
        cached = db.cache_get(cache_key)

    if cached is not None:
        return {
            "ok": True,
            "source": source.id,
            "angle": angle_used,
            "keyword": kw,
            "count": len(cached),
            "results": cached,
            "cached": True,
        }

    try:
        wait_interval(source)
        records = source.search(kw, angle_used, limit=limit, region_id=region_id)
    except SourceError as e:
        if history:
            db.add_history(source.id, angle_used, kw, False, 0, error=str(e))
        return {
            "ok": False,
            "source": source.id,
            "angle": angle_used,
            "keyword": kw,
            "error": str(e),
            "results": [],
        }

    if person:
        records = [r for r in records if record_person_hit(r, person)]

    if history:
        db.add_history(source.id, angle_used, kw, True, len(records))
    if cache and source.cacheable and records:
        db.cache_put(cache_key, records)

    # 接口可能返回 total（总命中数）但只给了前几条
    meta = getattr(source, "last_meta", lambda: {})()
    total = int(meta.get("total") or 0) if isinstance(meta, dict) else 0

    return {
        "ok": True,
        "source": source.id,
        "angle": angle_used,
        "keyword": kw,
        "count": len(records),
        "total": total or None,
        "truncated": bool(meta.get("truncated")) if isinstance(meta, dict) else False,
        "results": records,
        "cached": False,
    }


def probe_source(source_id: str) -> dict:
    """对数据源做一次最小化连通性测试：不写历史、不走缓存，参数贴近真实查询。"""
    source = sources.get_source(source_id)
    start = time.monotonic()
    try:
        wait_interval(source)
        rows = source.search("科技", "企业名", limit=1)
        elapsed = round(time.monotonic() - start, 2)
        if rows:
            return {
                "ok": True,
                "source": source.id,
                "elapsed": elapsed,
                "count": len(rows),
                "sample": rows[0].get("name", ""),
                "sampleFields": {
                    k: str(v)[:40]
                    for k, v in rows[0].items()
                    if not str(k).startswith("_")
                },
                "message": f"连接正常：返回 {len(rows)} 条，耗时 {elapsed}s，示例：{rows[0].get('name', '')}",
            }
        return {
            "ok": True,
            "source": source.id,
            "elapsed": elapsed,
            "count": 0,
            "sample": "",
            "sampleFields": {},
            "message": "接口可达但未返回数据（关键词：科技，可先换数据源或调 TYC_COOKIE）",
        }
    except SourceError as e:
        return {"ok": False, "source": source.id, "message": f"{e}"}
    except Exception as e:  # 兜底，不让诊断环节自身崩溃
        return {"ok": False, "source": source.id, "message": f"未知异常：{e}"}
