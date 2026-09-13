"""Cookie 持久化管理：唯一权威存储 = data/cookies.json。

- 设置页保存/清除 实时写入该文件；
- 环境变量（.env / docker env）只作为兜底（文件里没有该源时生效）；
- 启动迁移：旧版本存在 DB config 表里的 cookie_* 自动搬到 json；
- data 目录整体迁移（本地 ↔ VPS）时 Cookie 随卷走，不再丢失。
"""

import json
from datetime import datetime

from .. import config, db

_ENV_MAP = {
    "tyc": "TYC_COOKIE",
    "aqc": "AQC_COOKIE",
    "rb": "RB_COOKIE",
    "c88": "CHA88_COOKIE",
}

ALLOWED = set(_ENV_MAP)


def _file() -> "Path":
    config.ensure_dirs()
    return config.DATA_DIR / "cookies.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_store(store: dict) -> None:
    _file().write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")


def migrate_from_db() -> int:
    """旧版把 Cookie 存在 DB config 表（cookie_*），一次性搬到 json。"""
    moved = 0
    store = _load()
    try:
        for sid in ALLOWED:
            db_key = f"cookie_{sid}"
            val = db.get_config(db_key)
            if val and not store.get(sid, {}).get("cookie"):
                at = db.get_config(db_key + "_at") or ""
                store[sid] = {"cookie": val, "at": at}
                moved += 1
                db.set_config(db_key, "")  # 迁移后清除，避免双源混乱
    except Exception:
        pass
    if moved:
        _save_store(store)
    return moved


def cookie_for(source_id: str) -> str:
    entry = _load().get(source_id) or {}
    if entry.get("cookie"):
        return entry["cookie"]
    env_key = _ENV_MAP.get(source_id)
    if env_key:
        return getattr(config, env_key, "") or ""
    return ""


def save_cookie(source_id: str, cookie: str) -> None:
    if source_id not in ALLOWED:
        raise ValueError(f"未知数据源：{source_id}")
    store = _load()
    store[source_id] = {"cookie": cookie.strip(), "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _save_store(store)


def clear_cookie(source_id: str) -> None:
    if source_id not in ALLOWED:
        raise ValueError(f"未知数据源：{source_id}")
    store = _load()
    store.pop(source_id, None)
    _save_store(store)


def status(source_id: str) -> dict:
    entry = _load().get(source_id) or {}
    env_key = _ENV_MAP.get(source_id)
    envv = getattr(config, env_key, "") if env_key else ""
    active = entry.get("cookie") or envv or ""
    return {
        "id": source_id,
        "configured": bool(active),
        "fromPage": bool(entry.get("cookie")),
        "fromEnv": bool(envv) and not entry.get("cookie"),
        "configuredAt": entry.get("at"),
        "length": len(active),
    }
