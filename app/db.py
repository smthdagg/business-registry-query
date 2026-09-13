"""SQLite 轻量封装：每操作独立连接 + WAL，线程安全，自用规模足够。"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config


def _now() -> str:
    """本地时间字符串，用于展示。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATA_DIR / "app.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """读写事务。"""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def query() -> Iterator[sqlite3.Connection]:
    """只读连接。"""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    angle TEXT NOT NULL,
    keyword TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 0,
    count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS cache (
    ck TEXT PRIMARY KEY,
    results TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,          -- batch | relation
    title TEXT NOT NULL,
    params TEXT NOT NULL,        -- JSON
    status TEXT NOT NULL,        -- queued|running|done|error|cancelling|cancelled|interrupted
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    result TEXT,                 -- JSON
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    note TEXT,
    last_checked_at TEXT,
    last_status TEXT,
    changes_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    note TEXT,
    created_at TEXT NOT NULL,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user TEXT,
    role TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
"""


def init() -> None:
    config.ensure_dirs()
    with tx() as conn:
        conn.executescript(_SCHEMA)
        row = conn.execute("SELECT v FROM config WHERE k='source'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO config(k,v) VALUES('source',?)",
                (config.DEFAULT_SOURCE,),
            )


# ---------------- config 键值 ----------------

def get_config(key: str, default: str | None = None) -> str | None:
    with query() as conn:
        row = conn.execute("SELECT v FROM config WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def set_config(key: str, value: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO config(k,v) VALUES(?,?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )


# ---------------- 搜索历史 ----------------

def add_history(source: str, angle: str, keyword: str, ok: bool, count: int, error: str | None = None) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO search_history(ts,source,angle,keyword,ok,count,error) VALUES(?,?,?,?,?,?,?)",
            (_now(), source, angle, keyword, 1 if ok else 0, count, error),
        )
        conn.execute(
            "DELETE FROM search_history WHERE id NOT IN ("
            "SELECT id FROM search_history ORDER BY id DESC LIMIT ?)",
            (config.HISTORY_MAX_ROWS,),
        )


def list_history(limit: int = 20) -> list[dict]:
    with query() as conn:
        rows = conn.execute(
            "SELECT * FROM search_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- 实时源缓存 ----------------

def cache_get(key: str) -> list[dict] | None:
    with query() as conn:
        row = conn.execute(
            "SELECT results, created_at FROM cache WHERE ck=?", (key,)
        ).fetchone()
    if row is None:
        return None
    age = (datetime.now() - datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()
    if age > config.CACHE_TTL:
        with tx() as conn:
            conn.execute("DELETE FROM cache WHERE ck=?", (key,))
        return None
    return json.loads(row["results"])


def cache_put(key: str, results: list[dict]) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO cache(ck,results,created_at) VALUES(?,?,?) "
            "ON CONFLICT(ck) DO UPDATE SET results=excluded.results, created_at=excluded.created_at",
            (key, json.dumps(results, ensure_ascii=False), _now()),
        )
        conn.execute(
            "DELETE FROM cache WHERE ck NOT IN ("
            "SELECT ck FROM cache ORDER BY created_at DESC LIMIT ?)",
            (config.CACHE_MAX_ROWS,),
        )


# ---------------- 任务 ----------------

def create_task(kind: str, title: str, params: dict) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(kind,title,params,status,created_at) VALUES(?,?,?,?,?)",
            (kind, title, json.dumps(params, ensure_ascii=False), "queued", _now()),
        )
        return int(cur.lastrowid)


def update_task(task_id: int, **fields: Any) -> None:
    """按字段更新任务行（白名单，防注入）。"""
    allowed = {"status", "done", "error", "result", "finished_at", "total"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=?" for k in sets)
    with tx() as conn:
        conn.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*sets.values(), task_id))


def get_task(task_id: int) -> dict | None:
    with query() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        return None
    t = dict(row)
    if t.get("params") and isinstance(t["params"], str):
        try:
            t["params"] = json.loads(t["params"])
        except ValueError:
            t["params"] = {}
    if t.get("result") and isinstance(t["result"], str):
        try:
            t["result"] = json.loads(t["result"])
        except ValueError:
            pass
    return t


def list_tasks(limit: int = 50) -> list[dict]:
    with query() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    tasks = []
    for r in rows:
        tasks.append(get_task(r["id"]))
    return [t for t in tasks if t]


def mark_stale_interrupted() -> None:
    """启动时把上次异常退出留下的 running/cancelling 任务标记为 interrupted。"""
    with tx() as conn:
        conn.execute(
            "UPDATE tasks SET status='interrupted' "
            "WHERE status IN ('queued','running','cancelling')"
        )


def task_cancel_requested(task_id: int) -> bool:
    with query() as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    return row is not None and row["status"] == "cancelling"


# ---------------- 监控名单 ----------------

def watchlist_add(company_name: str, note: str = "") -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO watchlist(company_name,note,created_at) VALUES(?,?,?)",
            (company_name, note, _now()),
        )
        return int(cur.lastrowid)


def watchlist_list() -> list[dict]:
    with query() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def watchlist_get(wid: int) -> dict | None:
    with query() as conn:
        row = conn.execute("SELECT * FROM watchlist WHERE id=?", (wid,)).fetchone()
    return dict(row) if row else None


def watchlist_delete(wid: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM watchlist WHERE id=?", (wid,))


def watchlist_update(wid: int, **fields) -> None:
    allowed = {"last_checked_at", "last_status", "changes_count"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=?" for k in sets)
    with tx() as conn:
        conn.execute(f"UPDATE watchlist SET {cols} WHERE id=?", (*sets.values(), wid))


# ---------------- 操作日志 ----------------

def add_log(action: str, detail: str = "", user: str = "", role: str = "", ip: str = "") -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO logs(ts,user,role,action,detail,ip) VALUES(?,?,?,?,?,?)",
            (_now(), user, role, action, detail[:500], ip),
        )
        conn.execute(
            "DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 1000)"
        )


def list_logs(limit: int = 200) -> list[dict]:
    with query() as conn:
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- 历史查询清理 ----------------

def history_delete(history_id: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM search_history WHERE id=?", (history_id,))


def history_clear() -> None:
    with tx() as conn:
        conn.execute("DELETE FROM search_history")


# ---------------- 用户管理（管理员/会员） ----------------

def hash_password(password: str, salt: str | None = None) -> str:
    """PBKDF2-SHA256（20 万次迭代），存 salt$hash。"""
    import hashlib as _h
    import secrets as _s
    salt = salt or _s.token_hex(16)
    digest = _h.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    import hashlib as _h
    import hmac as _hmac
    try:
        salt, digest = stored.split("$", 1)
        calc = _h.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
        return _hmac.compare_digest(calc, digest)
    except Exception:
        return False


def user_create(username: str, password: str, role: str = "member", note: str = "") -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,role,note,created_at) VALUES(?,?,?,?,?)",
            (username, hash_password(password), role, note, _now()),
        )


def user_get(username: str) -> dict | None:
    with query() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(row) if row else None


def user_verify(username: str, password: str) -> dict | None:
    u = user_get(username)
    if u and verify_password(password, u["password_hash"]):
        with tx() as conn:
            conn.execute("UPDATE users SET last_login=? WHERE username=?", (_now(), username))
        return u
    return None


def user_list() -> list[dict]:
    with query() as conn:
        rows = conn.execute(
            "SELECT username,role,note,created_at,last_login FROM users ORDER BY role DESC, username"
        ).fetchall()
    return [dict(r) for r in rows]


def user_set_password(username: str, password: str) -> None:
    with tx() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE username=?",
                     (hash_password(password), username))


def user_set_role(username: str, role: str) -> None:
    with tx() as conn:
        conn.execute("UPDATE users SET role=? WHERE username=?", (role, username))


def user_delete(username: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM users WHERE username=?", (username,))


def user_count() -> int:
    with query() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def logs_clear() -> None:
    with tx() as conn:
        conn.execute("DELETE FROM logs")
