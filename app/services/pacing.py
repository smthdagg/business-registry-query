"""数据源调用公共逻辑：实时源限速（礼貌间隔）。"""

import threading
import time

from ..sources.base import BaseSource

_lock = threading.Lock()
_last_ts: dict[str, float] = {}


def wait_interval(source: BaseSource) -> None:
    """保证对同一数据源的两次真实请求之间至少间隔 min_interval 秒。"""
    interval = getattr(source, "min_interval", 0.0) or 0.0
    if interval <= 0:
        return
    while True:
        with _lock:
            now = time.monotonic()
            due = _last_ts.get(source.id, 0.0) + interval
            wait = due - now
            if wait <= 0:
                _last_ts[source.id] = now
                return
        time.sleep(min(wait, 0.2))
