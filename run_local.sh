#!/usr/bin/env bash
# 本地开发启动：uvicorn + 当前目录 data
set -e
cd "$(dirname "$0")"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"