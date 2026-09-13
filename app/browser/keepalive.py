"""Cookie 自动保活：定期用浏览器轻量访问各平台，平台自动续会话并回写 cookies.json。

- 只处理「已在设置里配置过 Cookie」的平台（未配置的游客态没有保活意义）；
- 每轮间隔 COOKIE_KEEPALIVE_MIN 分钟；单平台失败不中断循环；
- 检测到登录页 → 日志打印 need-login 提醒用户重新更新 Cookie。
- 仅在 BROWSER_FALLBACK=true 且装有 Camoufox 时生效（start() 自行门控）。
"""

import threading

from .. import config
from ..sources import cookies as cookie_svc

_thread: threading.Thread | None = None
_stop = threading.Event()


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
    from . import manager  # 惰性导入避免循环
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
            if any(m in url or m in content_marker for m in login_markers.get(sid, ())):
                print(f"[keepalive] {sid} 需要重新登录（会话失效）")
            else:
                # 续期后的 cookie 回写
                manager._export_cookies(sid)
                print(f"[keepalive] {sid} 会话保活成功")
        except Exception as e:
            print(f"[keepalive] {sid} 保活失败: {str(e)[:60]}")


def _loop() -> None:
    # 启动后先等 1 分钟，避免抢占业务请求
    _stop.wait(60)
    while not _stop.is_set():
        try:
            _tick()
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
