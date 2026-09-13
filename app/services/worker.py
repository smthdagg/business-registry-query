"""后台任务队列：单工作线程，串行执行批量查询与关联分析任务。

- 任务进度实时落库，前端轮询即可；
- 启动时把上次异常退出遗留的 running/queued 任务标记为 interrupted；
- 对实时源在“数据源最小间隔”之外再叠加任务级 delay，双保险防高频。
"""

import json
import queue
import threading
import time
from datetime import datetime

from .. import db, sources
from . import aggregate
from . import relation as relation_svc

_q: "queue.Queue[int]" = queue.Queue()
_thread: threading.Thread | None = None
_quit = threading.Event()

# 每行可自带角度前缀：法人:张伟明 / 股东：李四 / 企业名|xxx / 电话：… / 信用代码:…
_ANGLE_ALIAS = {
    "综合": "综合", "企业名": "企业名", "名称": "企业名", "公司": "企业名",
    "法人": "法人", "法定代表人": "法人", "股东": "股东",
    "信用代码": "信用代码", "统一社会信用代码": "信用代码", "代码": "信用代码",
    "电话": "电话", "手机": "电话",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_line(raw: str) -> tuple[str, str] | None:
    """解析批量任务的一行；返回 (关键词, 角度)，格式非法返回 None（跳过）。"""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    kw, angle = line, ""
    for sep in (":", "：", "|"):
        if sep in line:
            head, rest = line.split(sep, 1)
            if head.strip() in _ANGLE_ALIAS and rest.strip():
                kw, angle = rest.strip(), _ANGLE_ALIAS[head.strip()]
            break
    return (kw, angle) if kw else None


def start() -> None:
    """启动工作线程（幂等）。"""
    global _thread
    db.mark_stale_interrupted()
    if _thread and _thread.is_alive():
        return
    _quit.clear()
    _thread = threading.Thread(target=_loop, name="task-worker", daemon=True)
    _thread.start()


def stop() -> None:
    _quit.set()
    if _thread:
        _thread.join(timeout=3)


def enqueue(task_id: int) -> None:
    _q.put(task_id)


def _loop() -> None:
    while not _quit.is_set():
        try:
            task_id = _q.get(timeout=0.3)
        except queue.Empty:
            continue
        _execute(task_id)


def _execute(task_id: int) -> None:
    task = db.get_task(task_id)
    if not task or task["status"] in ("done", "error", "cancelled", "interrupted"):
        return
    if task["status"] == "cancelling":
        # 排队期间就被取消：直接落地为已取消，不再启动
        db.update_task(task_id, status="cancelled", finished_at=_now())
        return
    kind = task["kind"]
    params = task["params"] or {}
    db.update_task(task_id, status="running")
    try:
        if kind == "batch":
            _run_batch(task_id, params)
        elif kind == "relation":
            _run_relation(task_id, params)
        else:
            raise ValueError(f"未知任务类型：{kind}")
    except Exception as e:  # 兜底：任何异常都不让线程死掉
        db.update_task(
            task_id, status="error", error=f"任务执行失败：{e}",
            finished_at=_now(),
        )


def _sleep_cancellable(seconds: float, task_id: int) -> bool:
    """分片休眠，期间响应取消请求；返回是否已被取消。"""
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if db.task_cancel_requested(task_id):
            return True
        time.sleep(min(0.2, deadline - time.monotonic()))
    return False


# ---------------------------------------------------------------------------
# 批量查询
# ---------------------------------------------------------------------------

def _run_batch(task_id: int, params: dict) -> None:
    lines = params.get("lines") or []
    if not lines:
        db.update_task(task_id, status="error", error="批量任务没有关键词行", finished_at=_now())
        return

    source_id = params.get("source") or "mock"
    fallback_angle = params.get("angle", "综合")
    delay = float(params.get("delay", 0) or 0)
    source = None
    if source_id != aggregate.AGG_ID:
        source = sources.get_source(source_id)
    effective_delay = max(delay, getattr(source, "min_interval", 0.0) or 0.0)
    valid_angles = set(source.angles) if source else set(_ANGLE_ALIAS.values())

    total = len(lines)
    db.update_task(task_id, total=total, done=0)
    items: list[dict] = []
    consec_errors = 0
    interrupted = False

    for i, raw in enumerate(lines):
        if db.task_cancel_requested(task_id):
            interrupted = True
            break
        parsed = parse_line(raw)
        if parsed is None:
            db.update_task(task_id, done=i + 1)
            continue
        kw, angle = parsed
        if not angle:
            angle = fallback_angle
        if source is not None and angle not in valid_angles:
            angle = "综合"

        outcome = aggregate.do_search(source_id, angle, kw, limit=50, cache=True)
        if outcome["ok"]:
            consec_errors = 0
            records = outcome["results"]
            items.append({
                "keyword": kw,
                "angle": angle,
                "ok": True,
                "count": len(records),
                "best": records[0] if records else None,
                "matches": [
                    {k: r.get(k) for k in (
                        "name", "legalPersonName", "regStatus", "creditCode",
                        "phone", "companyScore", "industry",
                    ) if r.get(k) not in (None, "")}
                    for r in records[:5]
                ],
            })
        else:
            consec_errors += 1
            items.append({
                "keyword": kw, "angle": angle, "ok": False,
                "error": outcome.get("error", "查询失败"), "count": 0,
            })
            # 连续失败说明多半是接口级问题（Cookie 失效等），尽早中止避免浪费
            if consec_errors >= 4:
                items.append({
                    "keyword": "(系统)", "angle": "", "ok": False,
                    "error": f"连续 {consec_errors} 行失败，已中止后续行（请检查数据源配置）",
                })
                db.update_task(task_id, done=total, error="连续失败，任务中止")
                interrupted = True
                break

        db.update_task(task_id, done=i + 1)
        if i + 1 < total and effective_delay > 0:
            if _sleep_cancellable(effective_delay, task_id):
                interrupted = True
                break

    if interrupted and db.task_cancel_requested(task_id):
        db.update_task(task_id, status="cancelled", done=total, finished_at=_now())
        return
    if interrupted:
        db.update_task(
            task_id, status="error", done=total,
            error="连续失败，任务中止", finished_at=_now(),
        )
        return

    ok_count = sum(1 for it in items if it.get("ok"))
    status = "done" if ok_count or not items else "error"
    db.update_task(
        task_id, status=status, done=total,
        error=None if status == "done" else "所有行均查询失败",
        result=json.dumps({"type": "batch", "items": items}, ensure_ascii=False),
        finished_at=_now(),
    )


# ---------------------------------------------------------------------------
# 关联分析
# ---------------------------------------------------------------------------

def _run_relation(task_id: int, params: dict) -> None:
    target = (params.get("target") or "").strip()
    source_id = params.get("source") or "mock"

    def on_progress(requests: int) -> None:
        db.update_task(task_id, done=requests)

    graph = relation_svc.build_relation_graph(
        source_id=source_id,
        target=target,
        depth=int(params.get("depth", 2)),
        max_nodes=int(params.get("maxNodes", 40)),
        strategies=params.get("strategies"),
        on_progress=on_progress,
    )
    db.update_task(
        task_id,
        status="done",
        done=graph["meta"]["requests"],
        result=json.dumps({"type": "relation", "graph": graph}, ensure_ascii=False),
        finished_at=_now(),
    )
