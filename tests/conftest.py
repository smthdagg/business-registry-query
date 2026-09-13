"""测试公共环境：隔离数据目录 + 关闭认证，避免污染真实数据。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from app import config as cfg


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """每个测试独立临时数据目录；关闭登录口令。"""
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "ADMIN_PASSWORD", "")
    from app import db

    db.init()
    return tmp_path
