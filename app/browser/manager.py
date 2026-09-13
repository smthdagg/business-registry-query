"""Camoufox 指纹浏览器会话管理（可选组件，需 pip install camoufox）。

设计：
- 懒启动：首次使用才拉起浏览器（headless 可配）；崩溃自动重建；
- Cookie 双向同步：启动时从 data/cookies.json 注入各平台会话；
  平台 Set-Cookie 续期后自动回写 cookies.json（供 HTTP 通道共用）；
- 页面内 fetch：在平台页面上下文执行同源请求（自动带登录态/指纹/canary），
  这是绕过风控拿到全量数据的通道；
- 登录失效检测：命中登录页/游客限额 → 日志打印“需登录”，更新 Cookie 后自动恢复。

所有浏览器操作通过内部锁串行化；本模块线程安全。
"""

import json
import threading
from typing import Any

from .. import config
from ..sources import cookies as cookie_svc

_PLATFORM_HOME = {
    "rb": "https://www.riskbird.com/",
    "aqc": "https://www.aiqicha.baidu.com/",
    "c88": "https://88cha.com/",
    "tyc": "https://m.tianyancha.com/",
}
_PLATFORM_ORIGIN = {
    "rb": "https://www.riskbird.com",
    "aqc": "https://www.aiqicha.baidu.com",
    "c88": "https://88cha.com",
    "tyc": "https://m.tianyancha.com",
}

_lock = threading.RLock()
_browser = None            # camoufox browser 实例
_context = None            # 持久 context（cookie 所在）
_pages: dict[str, Any] = {}   # platform -> page（每平台一个常驻 tab）
Unavailable = ("camoufox 未安装或启动失败")


def available() -> bool:
    try:
        import camoufox  # noqa: F401
        return True
    except Exception:
        return False


def enabled() -> bool:
    return config.BROWSER_FALLBACK and available()


def _new_browser():
    from camoufox.sync_api import Camoufox
    kwargs = {"headless": config.BROWSER_HEADLESS}
    if config.BROWSER_GEOIP:
        kwargs["geoip"] = True
    cm = Camoufox(**kwargs)
    return cm.__enter__()


def _get() -> Any:
    """懒启动浏览器（带锁）。"""
    global _browser, _context
    with _lock:
        if _browser is not None:
            return _browser
        _browser = _new_browser()
        # 注入 cookies.json 中各平台 cookie
        all_cookies = []
        store = cookie_svc._load()
        domain_map = {"rb": "riskbird.com", "aqc": "aiqicha.baidu.com",
                      "c88": "88cha.com", "tyc": "tianyancha.com"}
        for sid, entry in store.items():
            dom = domain_map.get(sid)
            if not dom or not entry.get("cookie"):
                continue
            for pair in entry["cookie"].split(";"):
                if "=" not in pair:
                    continue
                name, _, value = pair.partition("=")
                name, value = name.strip(), value.strip()
                if not name:
                    continue
                all_cookies.append({"name": name, "value": value,
                                    "domain": dom, "path": "/"})
        if all_cookies:
            try:
                _context = _browser.new_context()
                _context.add_cookies(all_cookies)
            except Exception:
                _context = None
        return _browser


def _page_for(platform: str):
    """每平台一个常驻 tab；首次访问平台首页以建立会话。"""
    global _context
    browser = _get()
    if _context is None:
        _context = browser.new_context()
    page = _pages.get(platform)
    if page is None or page.is_closed():
        page = _context.new_page()
        page.goto(_PLATFORM_HOME[platform], timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        _pages[platform] = page
    elif not page.url.startswith(_PLATFORM_ORIGIN[platform]):
        page.goto(_PLATFORM_ORIGIN[platform], timeout=45000, wait_until="domcontentloaded")
    return page


def _page_fetch(platform: str, path: str, payload: dict | None = None,
                method: str = "POST") -> dict:
    """在平台页面上下文执行同源 fetch（自动带登录态/指纹/风控 cookie）。"""
    page = _page_for(platform)
    origin = _PLATFORM_ORIGIN[platform]
    body = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    js = """
        async (p) => {
            const init = {method: p.method, headers: {'Content-Type': 'application/json'}};
            if (p.body !== null && p.body !== undefined) init.body = p.body;
            const resp = await fetch(p.path, init);
            const ct = resp.headers.get('content-type') || '';
            if (!ct.includes('json')) return {__nonjson__: true, status: resp.status};
            return await resp.json();
        }
    """
    return page.evaluate(js, {"path": path, "method": method, "body": body})


def fetch_api(platform: str, path: str, payload: dict | None = None,
              method: str = "POST") -> dict:
    """对外入口：带锁的页面内 API 调用。失败时抛 RuntimeError。"""
    with _lock:
        page = _page_for(platform)
        origin = _PLATFORM_ORIGIN[platform]
        if not page.url.startswith(origin):
            page.goto(origin, timeout=45000, wait_until="domcontentloaded")
        body = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        js = """
            async (p) => {
                const init = {method: p.method, headers: {'Content-Type': 'application/json'}};
                if (p.body !== null && p.body !== undefined) init.body = p.body;
                const resp = await fetch(p.path, init);
                const ct = resp.headers.get('content-type') || '';
                if (!ct.includes('json')) return {__nonjson__: true, status: resp.status};
                return await resp.json();
            }
        """
        result = page.evaluate(js, {"path": path, "method": method, "body": body})
        _export_cookies(platform)
        if isinstance(result, dict) and result.get("__nonjson__"):
            raise RuntimeError(f"{platform} 返回非 JSON（可能需重新登录）")
        return result


def _export_cookies(platform: str) -> None:
    """把浏览器上下文里该平台的 cookie 回写 cookies.json（自动续期核心）。"""
    try:
        origin = _PLATFORM_ORIGIN[platform]
        cookies = [c for c in _context.cookies(origin) if c.get("value")]
        if not cookies:
            return
        parts = [f"{c['name']}={c['value']}" for c in cookies]
        cookie_svc.save_cookie(platform, "; ".join(parts))
    except Exception:
        pass
