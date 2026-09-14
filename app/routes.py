"""API 业务路由。

公开（无需登录）：/api/config、/api/health
受保护（ADMIN_PASSWORD 设置后）：其余全部接口
"""

import json
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from . import auth, config as app_config, db, sources
from .sources.base import (  # noqa: F401
    ANGLE_META,
    FIELD_LABELS,
    FIELD_META,
    LIST_COLUMNS,
    STRATEGY_META,
    SourceError,
)
from .services import aggregate, exporter, profile as profile_svc, worker
from .services.pacing import wait_interval
from pydantic import BaseModel, Field

public = APIRouter(prefix="/api", tags=["public"])
protected = APIRouter(
    prefix="/api",
    tags=["api"],
    dependencies=[Depends(auth.require_login)],
)
admin = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(auth.require_admin)],
)


# ---------------------------------------------------------------------------
# 公共信息
# ---------------------------------------------------------------------------

@public.get("/config")
def get_config() -> dict:
    current = db.get_config("source") or app_config.DEFAULT_SOURCE
    return {
        "auth": auth.auth_enabled(),
        "currentSource": current,
        "sources": sources.list_sources(),
        "fields": [{"key": k, "label": v} for k, v in FIELD_META],
        "listColumns": LIST_COLUMNS,
        "angleMeta": ANGLE_META,
        "strategyMeta": STRATEGY_META,
        "limits": {
            "batchMaxLines": app_config.BATCH_MAX_LINES,
            "relationMaxDepth": app_config.RELATION_MAX_DEPTH,
            "relationMaxNodes": app_config.RELATION_MAX_NODES,
        },
    }


# ---------------------------------------------------------------------------
# 单次查询
# ---------------------------------------------------------------------------

class SearchBody(BaseModel):
    keyword: str
    angle: str = "综合"
    source: str | None = None
    limit: int = Field(default=20, ge=1, le=1000)
    person: str | None = None
    region_id: str = ""


@protected.post("/search")
def search(body: SearchBody, request: Request) -> dict:
    db.add_log("查询", f"{body.angle}：{body.keyword}", role=getattr(request.state, "role", ""))
    return aggregate.do_search(
        body.source, body.angle, body.keyword, limit=body.limit,
        person=body.person, region_id=body.region_id
    )


@protected.get("/me")
def me(request: Request) -> dict:
    return {
        "role": getattr(request.state, "role", "admin"),
        "username": getattr(request.state, "user", ""),
        "auth": auth.auth_enabled(),
    }


@protected.get("/history")
def history(limit: int = 12) -> dict:
    return {"items": db.list_history(max(1, min(limit, 100)))}


@protected.delete("/history/{history_id}")
def history_delete(history_id: int, request: Request) -> dict:
    db.history_delete(history_id)
    db.add_log("删除历史", f"id={history_id}", role=getattr(request.state, "role", ""))
    return {"ok": True}


@protected.delete("/history")
def history_clear(request: Request) -> dict:
    db.history_clear()
    db.add_log("清空历史", role=getattr(request.state, "role", ""))
    return {"ok": True}


class ProfileBody(BaseModel):
    name: str
    source: str | None = None
    record: dict | None = None


@protected.post("/profile")
def company_profile(body: ProfileBody, request: Request) -> dict:
    """公司档案：结构化明细（股东/对外投资/分支机构）+ 跨源比对补充。"""
    db.add_log("查看档案", body.name, role=getattr(request.state, "role", ""))
    try:
        return profile_svc.build_profile(
            record=body.record, name=body.name, source_id=body.source
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except SourceError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None


class PersonSearchBody(BaseModel):
    name: str
    region_id: str = ""


@protected.post("/person-search")
def person_search(body: PersonSearchBody) -> dict:
    """按姓名搜索人员：风鸟同名分组（每组独立 personId，精确区分同名），
    支持按任职企业所在省份（regionId）筛选。"""
    try:
        rb = sources.get_source("rb")
        wait_interval(rb)
        items = rb.person_search(body.name.strip(), limit=30,
                                 region_id=body.region_id.strip())
        return {"source": "rb", "items": items}
    except Exception as e:
        return {"source": "rb", "items": [], "error": str(e)}


class PersonCompaniesBody(BaseModel):
    name: str
    pid: str | None = None


@protected.post("/person-companies")
def person_companies(body: PersonCompaniesBody) -> dict:
    """人员关联企业：合并风鸟精确（personId，含职务）与天眼查法定代表人口径，去重。"""
    combined: list[dict] = []
    seen: set[str] = set()
    used_rb = False

    if body.pid:
        try:
            rb = sources.get_source("rb")
            wait_interval(rb)
            companies = rb.person_companies(body.pid, limit=30)
            if companies:
                used_rb = True
                for c in companies:
                    key = c["name"].lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    c["sources"] = ["rb"]
                    combined.append(c)
        except Exception:
            pass

    res = aggregate.do_search(None, "法人", body.name, limit=30, person=body.name)
    for r in res.get("results") or []:
        key = (r.get("name") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        r["sources"] = list(r.get("sources") or [])
        if not r.get("role"):
            r["role"] = "法定代表人" if (r.get("legalPersonName") or "").strip() == body.name.strip() else ""
        combined.append(r)

    if not combined:
        return {
            "source": "aggregate",
            "name": body.name,
            "companies": [],
            "error": res.get("error") or "未检索到该人员关联企业",
        }
    return {
        "source": "merged" if used_rb else "aggregate",
        "name": body.name,
        "companies": combined,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Cookie 管理（部署 VPS 后无需改 .env / 重启，页面实时管理）
# ---------------------------------------------------------------------------

@admin.get("/settings/cookies")
def list_cookies() -> dict:
    from .sources import cookies as cookie_svc
    return {"items": [cookie_svc.status(sid) for sid in ("tyc", "aqc", "rb", "c88")]}


class CookieBody(BaseModel):
    source: str
    cookie: str = ""


@admin.post("/settings/cookie")
def save_cookie(body: CookieBody) -> dict:
    from .sources import cookies as cookie_svc
    try:
        cookie_svc.save_cookie(body.source, body.cookie)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    db.add_log("更新Cookie", body.source)
    return {"ok": True, **cookie_svc.status(body.source)}


class CookieImportBody(BaseModel):
    source: str
    text: str


@admin.post("/cookies/import")
def import_cookies(body: CookieImportBody, request: Request) -> dict:
    """万能 Cookie 导入：自动识别 Netscape / JSON / Cookie 头 / cURL 格式并入库。"""
    from .services.cookie_import import MAX_TEXT, parse_any
    from .sources import cookies as cookie_svc

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容为空")
    if len(text) > MAX_TEXT:
        raise HTTPException(status_code=400, detail="内容过大（上限 512KB）")
    if body.source not in cookie_svc.ALLOWED:
        raise HTTPException(status_code=400, detail=f"未知数据源：{body.source}")

    parsed = parse_any(text)
    if parsed["format"] in ("empty", "unknown", "too-large") or not parsed["count"]:
        reasons = {
            "empty": "内容为空",
            "too-large": "内容超过 512KB",
            "unknown": "未识别出任何 name=value 的 Cookie（检查格式）",
        }
        raise HTTPException(status_code=400,
                            detail=f"解析失败（格式：{parsed['format']}）：{reasons.get(parsed['format'], '')}")

    cookie_svc.save_cookie(body.source, parsed["cookie"])
    db.add_log("导入Cookie", f"{body.source}（{parsed['format']}，{parsed['count']} 条）",
               user=getattr(request.state, "user", ""), role="admin")
    return {
        "ok": True,
        "format": parsed["format"],
        "count": parsed["count"],
        "names": parsed["names"][:20],
        "domains": parsed["domains"][:10],
        **cookie_svc.status(body.source),
    }


@admin.post("/settings/cookie/clear")
def clear_cookie(body: CookieBody) -> dict:
    from .sources import cookies as cookie_svc
    try:
        cookie_svc.clear_cookie(body.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    db.add_log("清除Cookie", body.source)
    return {"ok": True, **cookie_svc.status(body.source)}


@admin.post("/source")
def switch_source(body: dict) -> dict:
    sid = (body.get("source") or "").strip()
    try:
        sources.set_source(sid)
    except SourceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True, "currentSource": sid}


@protected.post("/source/probe")
def probe_source(body: dict) -> dict:
    """对数据源做连通性测试，返回具体失败原因（风控/超时/接口变更等）。"""
    sid = (body.get("source") or "").strip() or db.get_config("source") or app_config.DEFAULT_SOURCE
    if sid == sources.ALL_ID:
        return aggregate.probe_all()
    try:
        return aggregate.probe_source(sid)
    except SourceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


# ---------------------------------------------------------------------------
# 任务（批量查询 / 关联分析）
# ---------------------------------------------------------------------------

class TaskCreateBody(BaseModel):
    kind: str
    title: str = ""
    # batch
    lines: list[str] | None = None
    angle: str = "综合"
    delay: float = 0.0
    # relation
    target: str = ""
    depth: int = 2
    maxNodes: int = 40
    strategies: list[str] | None = None
    # 公共
    source: str | None = None


def _validate_task(body: TaskCreateBody) -> dict:
    default_source = db.get_config("source") or app_config.DEFAULT_SOURCE
    chosen = body.source or default_source
    allowed_sources = {sources.ALL_ID} | {s["id"] for s in sources.list_sources()}
    if chosen not in allowed_sources:
        raise HTTPException(status_code=400, detail=f"未知数据源：{chosen}")
    if body.kind == "batch":
        lines = [ln for ln in (body.lines or []) if ln.strip() and not ln.strip().startswith("#")]
        if not lines:
            raise HTTPException(status_code=400, detail="批量任务没有有效关键词行")
        if len(lines) > app_config.BATCH_MAX_LINES:
            raise HTTPException(
                status_code=400,
                detail=f"批量任务最多 {app_config.BATCH_MAX_LINES} 行，当前 {len(lines)} 行",
            )
        if not (0 <= body.delay <= 30):
            raise HTTPException(status_code=400, detail="请求间隔需在 0~30 秒之间")
        return {
            "source": chosen,
            "lines": lines,
            "angle": body.angle,
            "delay": body.delay,
        }
    if body.kind == "relation":
        if not body.target.strip():
            raise HTTPException(status_code=400, detail="请输入目标企业名称")
        return {
            "source": chosen,
            "target": body.target.strip(),
            "depth": body.depth,
            "maxNodes": body.maxNodes,
            "strategies": body.strategies,
        }
    raise HTTPException(status_code=400, detail=f"不支持的任务类型：{body.kind}")


@protected.post("/tasks")
def create_task(body: TaskCreateBody, request: Request) -> dict:
    params = _validate_task(body)
    if body.kind == "batch":
        title = body.title.strip() or f"批量查询 {len(params['lines'])} 条"
    else:
        title = body.title.strip() or f"关联分析：{params['target']}"
    task_id = db.create_task(body.kind, title, params)
    worker.enqueue(task_id)
    db.add_log("创建任务", f"{body.kind}：{title}", role=getattr(request.state, "role", ""))
    return {"id": task_id, **db.get_task(task_id)}


@protected.get("/tasks")
def list_tasks(limit: int = 40) -> dict:
    return {"items": db.list_tasks(max(1, min(limit, 200)))}


@protected.get("/tasks/{task_id}")
def get_task(task_id: int) -> dict:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@protected.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int) -> dict:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] not in ("queued", "running"):
        return {"ok": True, "status": task["status"], "note": "任务已结束，无需取消"}
    db.update_task(task_id, status="cancelling")
    return {"ok": True, "status": "cancelling"}


@protected.post("/tasks/{task_id}/rerun")
def rerun_task(task_id: int) -> dict:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] in ("queued", "running"):
        raise HTTPException(status_code=400, detail="任务正在执行中，无需重跑")
    params = dict(task["params"] or {})
    new_id = db.create_task(
        task["kind"],
        task["title"] + "（重跑）",
        params,
    )
    worker.enqueue(new_id)
    return {"id": new_id}


@protected.get("/tasks/{task_id}/export")
def export_task(task_id: int, fmt: str = "csv") -> Response:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if fmt not in exporter.FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的格式：{fmt}")
    if task["status"] != "done" or not task.get("result"):
        raise HTTPException(status_code=400, detail="任务尚未完成，暂不能导出")
    result = task["result"]
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            raise HTTPException(status_code=400, detail="任务结果损坏，无法导出") from None
    if not isinstance(result, dict) or result.get("type") != "batch":
        raise HTTPException(status_code=400, detail="只有批量查询任务支持导出")

    payload = exporter.FORMATS[fmt](result["items"])
    content_types = {
        "csv": "text/csv; charset=utf-8",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "json": "application/json; charset=utf-8",
    }
    filename = f"批量查询结果_{task_id}.{fmt}"
    return Response(
        content=payload,
        media_type=content_types[fmt],
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )


@admin.get("/logs")
def get_logs(limit: int = 200, action: str = "") -> dict:
    items = db.list_logs(max(1, min(limit, 1000)))
    if action:
        items = [x for x in items if x["action"] == action]
    actions = sorted({x["action"] or "" for x in db.list_logs(1000)})
    return {"items": items, "actions": actions}


@admin.delete("/logs")
def clear_logs(request: Request) -> dict:
    db.logs_clear()
    db.add_log("清空日志", user=getattr(request.state, "user", ""), role="admin")
    return {"ok": True}


class PasswordChangeBody(BaseModel):
    old_password: str
    new_password: str


@protected.post("/me/password")
def change_my_password(body: PasswordChangeBody, request: Request) -> dict:
    """自助修改密码（管理员/会员均可）。"""
    username = getattr(request.state, "user", "")
    if not username or username == "local":
        raise HTTPException(status_code=400, detail="当前为免登录模式，无需修改密码")
    user = db.user_get(username)
    if user is None or not db.verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="原密码错误")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    db.user_set_password(username, body.new_password)
    db.add_log("修改密码", username, user=username, role=user["role"])
    return {"ok": True}


USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-]{2,32}$")


class UserCreateBody(BaseModel):
    username: str
    password: str
    role: str = "member"
    note: str = ""


@admin.get("/users")
def admin_list_users() -> dict:
    return {"items": db.user_list()}


@admin.post("/users")
def admin_create_user(body: UserCreateBody, request: Request) -> dict:
    username = body.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="用户名限 2~32 位字母/数字/下划线/连字符")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if body.role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="角色必须是 admin 或 member")
    if db.user_get(username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    db.user_create(username, body.password, body.role, body.note.strip())
    db.add_log("创建用户", f"{username}（{body.role}）",
               user=getattr(request.state, "user", ""), role="admin")
    return {"ok": True, "username": username, "role": body.role}


@admin.post("/users/{username}/password")
def admin_reset_password(username: str, body: PasswordChangeBody,
                         request: Request) -> dict:
    if db.user_get(username) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    db.user_set_password(username, body.new_password)
    db.add_log("重置密码", username, user=getattr(request.state, "user", ""), role="admin")
    return {"ok": True}


@admin.post("/users/{username}/role")
def admin_set_role(username: str, body: dict, request: Request) -> dict:
    role = (body.get("role") or "").strip()
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="角色必须是 admin 或 member")
    if username == getattr(request.state, "user", "") and role != "admin":
        raise HTTPException(status_code=400, detail="不能降级自己的管理员角色")
    if db.user_get(username) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.user_set_role(username, role)
    db.add_log("调整角色", f"{username} → {role}",
               user=getattr(request.state, "user", ""), role="admin")
    return {"ok": True}


@admin.delete("/users/{username}")
def admin_delete_user(username: str, request: Request) -> dict:
    if username == getattr(request.state, "user", ""):
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    target = db.user_get(username)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    admins = [u for u in db.user_list() if u["role"] == "admin"]
    if target["role"] == "admin" and len(admins) <= 1:
        raise HTTPException(status_code=400, detail="不能删除最后一个管理员")
    db.user_delete(username)
    db.add_log("删除用户", username, user=getattr(request.state, "user", ""), role="admin")
    return {"ok": True}


# ---------------------------------------------------------------------------
# 监控名单
# ---------------------------------------------------------------------------

class WatchBody(BaseModel):
    company_name: str
    note: str = ""


@protected.get("/watchlist")
def watchlist_list() -> dict:
    return {"items": db.watchlist_list()}


@protected.post("/watchlist")
def watchlist_add(body: WatchBody, request: Request) -> dict:
    name = body.company_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="企业名不能为空")
    wid = db.watchlist_add(name, body.note.strip())
    db.add_log("加入监控", name, role=getattr(request.state, "role", ""))
    return {"id": wid, **db.watchlist_get(wid)}


@protected.delete("/watchlist/{wid}")
def watchlist_delete(wid: int, request: Request) -> dict:
    db.watchlist_delete(wid)
    db.add_log("移除监控", f"id={wid}", role=getattr(request.state, "role", ""))
    return {"ok": True}


@protected.post("/watchlist/{wid}/check")
def watchlist_check(wid: int, request: Request) -> dict:
    """重新拉取档案，与上次状态比对，记录变化。"""
    item = db.watchlist_get(wid)
    if item is None:
        raise HTTPException(status_code=404, detail="监控项不存在")
    from datetime import datetime
    try:
        profile = profile_svc.build_profile(name=item["company_name"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    b = profile.get("basic") or {}
    snapshot = {
        "regStatus": b.get("regStatus"),
        "legalPersonName": b.get("legalPersonName"),
        "regCapital": b.get("regCapital"),
        "estiblishTime": b.get("estiblishTime"),
    }
    changed = False
    if item.get("last_status"):
        try:
            prev = json.loads(item["last_status"])
            for k, v in snapshot.items():
                if prev.get(k) != v:
                    changed = True
                    break
        except ValueError:
            changed = True
    changes = (item.get("changes_count") or 0) + (1 if changed else 0)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.watchlist_update(wid, last_checked_at=now,
                        last_status=json.dumps(snapshot, ensure_ascii=False),
                        changes_count=changes)
    db.add_log("监控检查", f"{item['company_name']} 变化={'是' if changed else '否'}",
               role=getattr(request.state, "role", ""))
    return {"ok": True, "changed": changed, "changes_count": changes,
            "last_checked_at": now, "snapshot": snapshot}
