"""批量任务管线 + 导出测试（全程使用 mock 源，不联网）。"""

import pytest

from app import db
from app.services import exporter, worker
from app.services.worker import parse_line


def test_parse_line_prefix():
    assert parse_line(" 法人：张伟明 ") == ("张伟明", "法人")
    assert parse_line("股东：王强") == ("王强", "股东")
    assert parse_line("企业名|星辰控股集团有限公司") == ("星辰控股集团有限公司", "企业名")
    assert parse_line("电话:021-60001002") == ("021-60001002", "电话")
    assert parse_line("信用代码：913101156811234568") == ("913101156811234568", "信用代码")
    assert parse_line("普通关键词") == ("普通关键词", "")
    assert parse_line("# 注释行") is None
    assert parse_line("") is None


def _run_task(kind: str, title: str, params: dict) -> int:
    tid = db.create_task(kind, title, params)
    worker._execute(tid)
    return tid


def test_batch_flow():
    tid = _run_task("batch", "测试批量", {
        "source": "mock",
        "angle": "综合",
        "delay": 0,
        "lines": [
            "星辰控股集团有限公司",
            "法人：张伟明",
            "股东：刘洋",
            "电话：021-60001002",
            "绝对不存在的公司ZZZ",
            "# 注释行",
            "",
        ],
    })
    task = db.get_task(tid)
    assert task["status"] == "done"
    assert task["done"] == 7  # 总行数（注释/空行不计入 items）
    items = task["result"]["items"]
    assert items[0]["ok"] and items[0]["best"]["name"] == "星辰控股集团有限公司"
    assert items[1]["ok"] and items[1]["count"] == 2      # 张伟明法人：2 家
    assert items[2]["ok"] and items[2]["count"] >= 2      # 刘洋股东命中的企业
    assert items[3]["ok"] and items[3]["count"] == 2      # 同电话两家
    # 零命中是“成功但无结果”，不视为失败
    assert items[4]["ok"] is True and items[4]["count"] == 0 and items[4]["best"] is None


def test_batch_interface_failure_aborts(monkeypatch):
    """连续接口级失败（如 Cookie 失效）应中止任务并标记失败。"""
    def fake_search(*args, **kwargs):
        return {"ok": False, "error": "接口状态异常：mock-fail", "results": []}

    monkeypatch.setattr(worker.aggregate.search_svc, "do_search", fake_search)
    tid = _run_task("batch", "全失败", {
        "source": "mock", "angle": "综合", "delay": 0,
        "lines": ["行1", "行2", "行3", "行4", "行5", "行6"],
    })
    task = db.get_task(tid)
    assert task["status"] == "error"
    assert "连续失败" in (task["error"] or "")


def test_batch_cancel():
    tid = db.create_task("batch", "取消测试", {
        "source": "mock", "angle": "综合", "delay": 0.05,
        "lines": ["星辰控股集团有限公司"] * 20,
    })
    db.update_task(tid, status="cancelling")
    worker._execute(tid)
    assert db.get_task(tid)["status"] == "cancelled"


def test_relation_task_row():
    tid = _run_task("relation", "关联测试", {
        "source": "mock", "target": "星辰控股集团有限公司",
        "depth": 2, "maxNodes": 40,
        "strategies": ["legal", "invest"],
    })
    task = db.get_task(tid)
    assert task["status"] == "done"
    assert task["result"]["graph"]["meta"]["stats"]["companies"] >= 5


def test_export_formats():
    items = [
        {"keyword": "星辰控股集团有限公司", "angle": "企业名", "ok": True, "count": 1,
         "best": {"name": "星辰控股集团有限公司", "legalPersonName": "张伟明",
                  "regStatus": "存续", "creditCode": "913100006811234567",
                  "phone": "021-60001001"},
         "matches": []},
        {"keyword": "不存在AAA", "angle": "综合", "ok": False,
         "error": "未查询到相关企业数据", "count": 0},
    ]
    csv_bytes = exporter.to_csv(items)
    text = csv_bytes.decode("utf-8-sig")
    assert "公司名称" in text.splitlines()[0]
    assert "星辰控股集团有限公司" in text
    assert "张伟明" in text

    xlsx = exporter.to_xlsx(items)
    assert xlsx[:2] == b"PK"  # zip/xlsx 魔数

    js = exporter.to_json(items)
    assert "星辰控股集团有限公司".encode() in js
