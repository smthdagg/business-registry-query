"""登录与会话：单用户口令 + HMAC 签名 Cookie（纯标准库实现）。

ADMIN_PASSWORD 留空时自动跳过登录（仅限本地调试）；设置了口令后，
所有 /api 业务接口都要求有效会话。
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import config, db

COOKIE_NAME = "gs_session"
router = APIRouter(prefix="/api", tags=["auth"])


def auth_enabled() -> bool:
    try:
        return bool(config.ADMIN_PASSWORD or config.MEMBER_PASSWORD) or db.user_count() > 0
    except Exception:
        return bool(config.ADMIN_PASSWORD or config.MEMBER_PASSWORD)


def _ensure_seed_users() -> None:
    """首次启动：把环境变量口令迁移为数据库账号（admin / member）。"""
    try:
        if db.user_count() > 0:
            return
        if config.ADMIN_PASSWORD:
            db.user_create("admin", config.ADMIN_PASSWORD, "admin", "初始管理员（环境变量迁移）")
        if config.MEMBER_PASSWORD:
            db.user_create("member", config.MEMBER_PASSWORD, "member", "初始会员（环境变量迁移）")
    except Exception:
        pass


def _secret() -> bytes:
    if config.SECRET_KEY:
        return config.SECRET_KEY.encode("utf-8")
    key_file: Path = config.DATA_DIR / "secret.key"
    if key_file.exists():
        try:  # 既有部署修正权限（可伪造会话的高危文件）
            import os
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return key_file.read_bytes()
    config.ensure_dirs()
    key = secrets.token_hex(32).encode("utf-8")
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    return key


def _sign(payload: bytes) -> str:
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(payload).decode("ascii")
        + "."
        + base64.urlsafe_b64encode(sig).decode("ascii")
    )


def _verify(token: str) -> bool:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        sig = base64.urlsafe_b64decode(sig_b64.encode("ascii"))
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return False
        data = json.loads(payload)
        return data.get("exp", 0) > time.time()
    except Exception:
        return False


def issue_token(role: str = "admin", username: str = "") -> str:
    payload = json.dumps({
        "exp": int(time.time()) + config.LOGIN_TTL_DAYS * 86400,
        "role": role,
        "username": username,
    }).encode()
    return _sign(payload)


class LoginBody(BaseModel):
    username: str = ""
    password: str
    role: str = "admin"


@router.get("/health")
def health() -> dict:
    return {"ok": True, "name": "工商企业聚合信息查询系统", "auth": auth_enabled()}


# 登录失败限速（内存态，进程重启即清）：连续 5 次失败锁定 60 秒
_FAIL_TS: list[float] = []
_LOCK_UNTIL = 0.0
_MAX_FAILS, _LOCK_SECONDS = 5, 60


def _login_throttled() -> float:
    """返回剩余锁定秒数；0 表示未锁定。"""
    import time as _t
    global _LOCK_UNTIL
    now = _t.monotonic()
    while _FAIL_TS and now - _FAIL_TS[0] > _LOCK_SECONDS:
        _FAIL_TS.pop(0)
    if _LOCK_UNTIL > now:
        return _LOCK_UNTIL - now
    if len(_FAIL_TS) >= _MAX_FAILS:
        _LOCK_UNTIL = now + _LOCK_SECONDS
        _FAIL_TS.clear()
        return _LOCK_SECONDS
    return 0.0


def _record_login_fail() -> None:
    import time as _t
    _FAIL_TS.append(_t.monotonic())


@router.post("/login")
def login(body: LoginBody, response: Response) -> dict:
    if not auth_enabled():
        return {"ok": True, "note": "未启用登录", "role": "admin"}
    wait = _login_throttled()
    if wait > 0:
        db.add_log("登录限速", f"剩余 {wait:.0f} 秒")
        raise HTTPException(status_code=429, detail=f"失败次数过多，请 {wait:.0f} 秒后重试")

    username = (body.username or "").strip()
    # 兼容旧登录：未填用户名时按角色映射默认账号名
    if not username:
        username = "admin" if body.role == "admin" else "member"

    user = db.user_verify(username, body.password)
    if user is None and username == "admin" and config.ADMIN_PASSWORD \
            and hmac.compare_digest(body.password.encode("utf-8"), config.ADMIN_PASSWORD.encode("utf-8")):
        # 环境变量口令兼容：首次登录自动落库
        db.user_create("admin", body.password, "admin", "环境变量迁移")
        user = db.user_verify(username, body.password)
    if user is None and username == "member" and config.MEMBER_PASSWORD \
            and hmac.compare_digest(body.password.encode("utf-8"), config.MEMBER_PASSWORD.encode("utf-8")):
        db.user_create("member", body.password, "member", "环境变量迁移")
        user = db.user_verify(username, body.password)

    if user is None:
        _record_login_fail()
        raise HTTPException(status_code=401, detail="用户名或口令错误")

    role = user["role"]
    response.set_cookie(
        COOKIE_NAME, issue_token(role, username), max_age=config.LOGIN_TTL_DAYS * 86400,
        httponly=True, samesite="lax", path="/",
    )
    db.add_log("登录", f"用户={username} 角色={role}", user=username, role=role)
    return {"ok": True, "role": role, "username": username}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


def _session_user(token: str) -> tuple[str, str]:
    """从已验签 token 解析 (username, role)。"""
    try:
        payload_b64, _ = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        data = json.loads(payload)
        return data.get("username", ""), data.get("role", "admin")
    except Exception:
        return "", "admin"


def require_login(request: Request) -> None:
    """受保护接口依赖：未启用口令时放行，并实时校验用户表角色。"""
    if not auth_enabled():
        request.state.role = "admin"
        request.state.user = "local"
        return
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or not _verify(token):
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    username, token_role = _session_user(token)
    user = db.user_get(username) if username else None
    if user is None:
        # 用户已被删除：会话立即失效
        raise HTTPException(status_code=401, detail="账号不存在或已被删除")
    request.state.user = username
    request.state.role = user["role"]


def require_admin(request: Request) -> None:
    """仅管理员可用（设置/日志等）。"""
    require_login(request)
    if getattr(request.state, "role", "member") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
