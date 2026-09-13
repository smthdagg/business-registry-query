"""聚合查询（all 虚拟源）：合并去重、并行分发、关联自动选源。"""

import pytest

from app.services import aggregate
from app.services.relation import build_relation_graph


def test_merge_dedup_and_field_fill():
    per_source = {
        "rb": {"ok": True, "results": [{
            "name": "华为技术有限公司", "creditCode": "914403001922038216",
            "legalPersonName": "赵明路", "regStatus": "在营",
        }]},
        "c88": {"ok": True, "results": [{
            "name": "华为技术有限公司", "creditCode": "914403001922038216",
            "legalPersonName": "赵明路", "regCapital": "4114113.182万",
            "tags": ["高新技术企业"],
        }]},
        "tyc": {"ok": True, "results": [
            {"name": "华为技术有限公司", "creditCode": "914403001922038216", "phone": "0755-28780808"},
            # 其他公司
            {"name": "西安华为技术有限公司", "creditCode": "91610131668683606U"},
        ]},
        "aqc": {"ok": False, "error": "配额用尽", "results": []},
    }
    rows = aggregate.merge_results(per_source, "华为技术有限公司")
    assert len(rows) == 2
    first = rows[0]
    assert first["name"] == "华为技术有限公司"  # 精确匹配排最前
    assert first["sources"] == ["rb", "c88", "tyc"]
    # 字段级补全：风鸟没有注册资本，由 88查 补上
    assert first["regCapital"] == "4114113.182万"
    assert first["tags"] == ["高新技术企业"]
    assert first["phone"] == "0755-28780808"
    assert rows[1]["sources"] == ["tyc"]


def test_aggregate_search_dispatch(monkeypatch):
    """all：并行调各源并合并；具体源：直接透传。"""
    from app.services import search as search_svc

    def fake_do_search(sid, angle, kw, limit=20, cache=True, history=True, person=None, region_id=""):
        assert history is False  # 聚合内部禁用逐源历史
        data = {
            "rb": {"ok": True, "source": "rb", "count": 1, "total": 52,
                   "results": [{"name": "华为技术有限公司", "creditCode": "9144"}]},
            "c88": {"ok": True, "source": "c88", "count": 0, "results": []},
            "tyc": {"ok": False, "source": "tyc", "error": "接口状态异常", "results": []},
            "aqc": {"ok": False, "source": "aqc", "error": "免登录配额已用尽", "results": []},
        }
        return data[sid]

    monkeypatch.setattr(aggregate.search_svc, "do_search", fake_do_search)
    res = aggregate.do_search("all", "企业名", "华为技术有限公司", history=True)
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["results"][0]["sources"] == ["rb"]
    assert len(res["sourcesDetail"]) == 4
    ty = next(d for d in res["sourcesDetail"] if d["id"] == "tyc")
    assert ty["ok"] is False and "接口状态异常" in ty["error"]


def test_aggregate_passthrough_mock():
    res = aggregate.do_search("mock", "企业名", "星辰控股集团有限公司")
    assert res["ok"] is True and res["source"] == "mock"
    assert res["results"][0]["name"] == "星辰控股集团有限公司"


def test_relation_auto_pick_source(monkeypatch):
    """聚合关联：自动选第一个能命中目标企业的源（本测试统一 mock 掉）。"""
    from app.sources import MockSource
    import app.services.relation as relation_mod

    chosen = []

    class FakeSources:
        @staticmethod
        def get_source(sid):
            chosen.append(sid)
            return MockSource()

    monkeypatch.setattr(relation_mod.sources, "get_source", FakeSources.get_source)
    g = build_relation_graph(
        source_id="all", target="星辰控股集团有限公司",
        depth=2, max_nodes=40,
        strategies=["legal", "shareholder", "invest", "phone"],
    )
    assert g["meta"]["source"] == "mock"  # fake get_source 恒返回 MockSource
    assert chosen[0] == "rb"  # 但候选顺序按优先级：风鸟优先
    assert g["meta"]["stats"]["companies"] >= 5
    assert g["meta"]["rootName"] == "星辰控股集团有限公司"


def test_relation_all_no_hit(monkeypatch):
    import app.services.relation as relation_mod
    monkeypatch.setattr(
        relation_mod.sources, "get_source",
        lambda sid: __import__("app.sources", fromlist=["MockSource"]).MockSource(),
    )
    with pytest.raises(ValueError) as exc:
        build_relation_graph("all", "绝对不存在企业QQQ", depth=1, max_nodes=20)
    assert "所有数据源" in str(exc.value)


def test_relation_person_expansion(monkeypatch):
    """图谱自然人节点用风鸟 person_companies 精确展开（任职企业 + 角色边）。"""
    import app.services.relation as relation_mod

    class FakeRB:
        id = "rb"
        angles = ["综合", "企业名"]
        strategies = ["invest", "shareholder"]
        min_interval = 0.0
        cacheable = False

        def search(self, keyword, angle="综合", limit=20):
            return [{"name": "根公司", "legalPersonName": "赵明路", "_pid": "E1", "creditCode": "R1"}]

        def enrich(self, rec):
            rec["legalPersonId"] = "P1"
            rec["shareholders"] = [{"name": "李四", "percent": "10%", "pid": "P2"}]
            rec["investCompanies"] = []
            return rec

        def person_companies(self, pid, limit=20):
            if pid == "P1":
                return [
                    {"name": "根公司", "legalPersonName": "赵明路", "role": "法定代表人、董事长、董事", "source": "rb", "_pid": "E1", "creditCode": "R1"},
                    {"name": "子公司A", "legalPersonName": "赵明路", "role": "法定代表人", "source": "rb", "_pid": "E2", "creditCode": "R2"},
                ]
            if pid == "P2":
                return [{"name": "关联公司B", "role": "董事", "position": "董事", "source": "rb", "_pid": "E3", "creditCode": "R3"}]
            return []

    monkeypatch.setattr(relation_mod.sources, "get_source", lambda sid: FakeRB())
    g = build_relation_graph("rb", "根公司", depth=2, max_nodes=30,
                             strategies=["legal", "shareholder", "invest"])
    companies = {n["label"] for n in g["nodes"] if n["type"] == "co"}
    assert {"根公司", "子公司A", "关联公司B"} <= companies
    types = {e["type"] for e in g["edges"]}
    assert "legal" in types and "position" in types
    # 赵明路 法人边 + 李四 股东边
    pairs = {(g["nodes"][e["s"]]["label"], g["nodes"][e["t"]]["label"], e["type"])
             for e in g["edges"]}
    assert ("根公司", "赵明路", "legal") in pairs
    assert ("根公司", "李四", "shareholder") in pairs
    assert ("关联公司B", "李四", "position") in pairs
    # 本公司高管职务边（赵明路在根公司任 董事长、董事）
    exec_edges = [(g["nodes"][e["s"]]["label"], g["nodes"][e["t"]]["label"], e.get("label"))
                  for e in g["edges"] if e["type"] == "position"]
    assert ("根公司", "赵明路", "董事长、董事") in exec_edges
