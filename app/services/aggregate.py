"""聚合查询：一次关键词并行打多个真实数据源，结果合并去重。

- 虚拟源 id "all"（不出现在 BaseSource 注册表里，作为一层路由）；
- 各源并行请求（不同源之间互不等待，各自的礼貌间隔照常生效）；
- 合并去重：统一社会信用代码优先，其次企业名；重复企业做字段级补全，
  并在记录上带 sources 列表标明来源；
- 每源的成功/失败/条数/耗时放在 sourcesDetail，前端展示为统计芯片。
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import config, db
from . import search as search_svc

AGG_ID = "all"

# 合并优先级：风鸟（SVIP、字段最全）> 88查 > 天眼查 > 爱企查
REAL_ORDER = ["rb", "c88", "tyc", "aqc"]

# 按角度选择参与源：人名/条件类角度只有部分源有能力，避免白烧配额
_ANGLE_SOURCES = {
    "法人": ["rb", "tyc"],        # 风鸟人员分组 + 天眼查法人匹配
    "股东": ["rb"],               # 仅风鸟有股东维度
    "电话": ["tyc", "rb"],        # 电话反查
    "信用代码": ["rb", "c88", "tyc"],
    "企业名": REAL_ORDER,
    "综合": REAL_ORDER,
}


def sources_for_angle(angle: str) -> list[str]:
    return _ANGLE_SOURCES.get(angle, REAL_ORDER)

# 具体源连通性探测直接复用 search 服务实现
probe_source = search_svc.probe_source


def resolve_source_id(source_id: str | None) -> str:
    """未指定源时回落到当前配置的源（可能是 all）。"""
    return source_id or db.get_config("source") or config.DEFAULT_SOURCE


def merge_results(per_source: dict[str, dict], keyword: str) -> list[dict]:
    """按优先级合并各源结果；人员条目与公司条目分开处理。"""
    merged: dict[str, dict] = {}
    order: list[str] = []
    person_rows: list[dict] = []
    for sid in REAL_ORDER:
        res = per_source.get(sid) or {}
        if not res.get("ok"):
            continue
        for rec in res.get("results") or []:
            if rec.get("type") == "person":
                key = "p:" + (rec.get("pid") or rec.get("name", ""))
                if key in merged:
                    continue
                merged[key] = dict(rec)
                order.append(key)
                continue
            key = (
                (rec.get("creditCode") or "").replace(" ", "").lower()
                or (rec.get("name") or "").lower()
            )
            if not key:
                continue
            if key in merged:
                row = merged[key]
                for k, v in rec.items():
                    if k in ("source", "sources"):
                        continue
                    if v not in (None, "", []) and not row.get(k):
                        row[k] = v
                if sid not in row["sources"]:
                    row["sources"].append(sid)
            else:
                row = dict(rec)
                row.pop("source", None)
                row["sources"] = [sid]
                merged[key] = row
                order.append(key)
    rows = [merged[k] for k in order]
    low = keyword.strip().lower()
    rows.sort(key=lambda r: 0 if r.get("name", "").lower() == low else 1)
    return rows


def do_search(
    source_id: str | None,
    angle: str,
    keyword: str,
    limit: int = 20,
    cache: bool = True,
    history: bool = True,
    person: str | None = None,
    region_id: str = "",
) -> dict:
    """入口：具体源直接透传；"all" 走并行聚合。"""
    source_id = resolve_source_id(source_id)
    if source_id != AGG_ID:
        return search_svc.do_search(
            source_id, angle, keyword, limit=limit, cache=cache,
            person=person, region_id=region_id
        )

    kw = (keyword or "").strip()
    if not kw:
        return {"ok": False, "error": "关键词不能为空", "results": [], "sourcesDetail": []}

    order = sources_for_angle(angle)
    per_source: dict[str, dict] = {}

    def run(sid: str) -> dict:
        t0 = time.monotonic()
        try:
            r = search_svc.do_search(
                sid, angle, kw, limit=limit, cache=cache, history=False,
                person=person, region_id=region_id
            )
            r["elapsed"] = round(time.monotonic() - t0, 2)
            return r
        except Exception as e:  # 单源异常不影响整体
            return {
                "ok": False, "source": sid, "error": str(e),
                "results": [], "elapsed": round(time.monotonic() - t0, 2),
            }

    with ThreadPoolExecutor(max_workers=len(order)) as ex:
        futures = {ex.submit(run, sid): sid for sid in order}
        for fut in as_completed(futures):
            per_source[futures[fut]] = fut.result()

    rows = merge_results(per_source, kw)
    detail = []
    for sid in order:
        p = per_source.get(sid) or {}
        detail.append({
            "id": sid,
            "ok": bool(p.get("ok")),
            "count": p.get("count", 0),
            "error": p.get("error"),
            "elapsed": p.get("elapsed"),
        })
    totals = [p.get("total") or 0 for p in per_source.values()]
    if history:
        db.add_history(AGG_ID, angle, kw, bool(rows), len(rows))

    return {
        "ok": any(d["ok"] for d in detail),
        "source": AGG_ID,
        "angle": angle,
        "keyword": kw,
        "count": len(rows),
        "total": max(totals) if totals else None,
        "results": rows,
        "sourcesDetail": detail,
    }


def probe_all() -> dict:
    """聚合源连通性：逐源探测后汇总。"""
    results = [search_svc.probe_source(sid) for sid in REAL_ORDER]
    ok = any(r.get("ok") for r in results)
    parts = []
    for r in results:
        parts.append(f"{r['source']}:{'OK' if r.get('ok') else r.get('message', '失败')}")
    return {
        "ok": ok,
        "source": AGG_ID,
        "message": "；".join(parts),
        "detail": results,
    }
