#!/usr/bin/env bash
# 标准化管理脚本：start | stop | restart | status | reset-pw [新密码] | logs
set -e
cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"

case "${1:-status}" in
  start)
    if lsof -ti :"$PORT" >/dev/null 2>&1; then echo "已在运行（端口 $PORT）"; exit 0; fi
    [ -f .venv/bin/uvicorn ] || { echo "缺少虚拟环境：先运行 uv venv .venv --python 3.12 && uv pip install -p .venv/bin/python -r requirements.txt"; exit 1; }
    DATA_DIR=./data nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT" > /tmp/business-query.log 2>&1 &
    sleep 2; curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && echo "已启动：http://127.0.0.1:$PORT （日志 /tmp/business-query.log）" || { echo "启动失败，查看 /tmp/business-query.log"; exit 1; }
    ;;
  stop)
    lsof -ti :"$PORT" | xargs kill 2>/dev/null && echo "已停止（端口 $PORT）" || echo "本就未运行"
    ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
  status)
    if lsof -ti :"$PORT" >/dev/null 2>&1; then
      echo "运行中（端口 $PORT）"; curl -s "http://127.0.0.1:$PORT/api/health" && echo
    else echo "未运行"; fi ;;
  reset-pw)
    PW="${2:-changeme}"
    DATA_DIR=./data .venv/bin/python - "$PW" <<'PY'
import sys
from app import db
db.init()
if db.user_get("admin") is None:
    db.user_create("admin", sys.argv[1] if len(sys.argv) > 1 else "changeme", "admin", "手动重置")
else:
    db.user_set_password("admin", sys.argv[1] if len(sys.argv) > 1 else "changeme")
print("admin 密码已重置")
PY
    echo "账号：admin / ${PW:-changeme}（重启服务后生效：$0 restart）"
    ;;
  logs) tail -f /tmp/business-query.log 2>/dev/null || tail -f /tmp/gs_run.log ;;
  *) echo "用法：$0 {start|stop|restart|status|reset-pw [新密码]|logs}" ;;
esac
