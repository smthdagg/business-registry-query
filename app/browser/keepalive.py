"""Cookie 自动保活：定期用浏览器轻量访问各平台，平台自动续会话并回写 cookies.json。

- 只处理「已在设置里配置过 Cookie」的平台（未配置的游客态没有保活意义）；
- 每轮间隔 COOKIE_KEEPALIVE_MIN 分钟；单平台失败不中断循环；
- 检测到登录页 → 状态标记 need-login（设置页可见，提醒用户重新登录）。
"""

import threading
import time

from .. import config
from ..sources import cookies as cookie_svc
from . import manager

_thread: threading.Thread | None = None
_stop = threading.Event()
_last_round: str = ""


def _platforms() -> list[str]:
    out = []
    for sid in ("rb", "c88", "tyc", "aqc"):
        st = cookie_svc.status(sid)
        if st["configured"]:
            out.append(sid)
    return out


def _tick() -> None:
    platforms = _platforms()
    if not platforms:
        return
    from .manager import fetch_api, open_login  # 惰性导入避免循环
    for sid in platforms:
        if _stop.is_set():
            return
        # 登录页特征（按平台）
        login_markers = {
            "rb": ("登录", "auth/login"),
            "aqc": ("passport", "wappass"),
            "c88": ("passport", "login"),
            "tyc": ("登录",),
        }
        try:
            manager.fetch_api(sid, "/", None, "GET")
            page = manager._page_for(sid)
            url = page.url or ""
            content_marker = ""
            try:
                content_marker = page.content()[:4000]
            except Exception:
                pass
            need_login = any(m in url or m in content_marker for m in login_markers.get(sid, ()))
            if need_login:
                manager._status[sid] = "need-login"
                print(f"[keepalive] {sid} 需要重新登录（会话失效）")
            else:
                manager._status[sid] = "ok"
                # 续期后的 cookie 回写
                manager._export_cookies(sid)
                print(f"[keepalive] {sid} 会话保活成功")
        except Exception as e:
            manager._status[sid] = f"error: {str(e)[:60]}"
            print(f"[keepalive] {sid} 保活失败: {str(e)[:60]}")


def _loop() -> None:
    # 启动后先等 1 分钟，避免抢占业务请求
    _stop.wait(60)
    while not _stop.is_set():
        global _last_round
        try:
            from datetime import datetime
            _tick()
            _last_round = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            print("[keepalive] 循环异常:", str(e)[:80])
        _stop.wait(max(5, config.COOKIE_KEEPALIVE_MIN) * 60)


def start() -> None:
    global _thread
    if not config.BROWSER_FALLBACK:
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="cookie-keepalive", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


def last_round() -> str:
    return _last_round
