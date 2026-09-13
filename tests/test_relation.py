"""关联图谱分析测试（mock 源，全策略）。"""

import pytest

from app.services.relation import build_relation_graph


def names_of(graph, type_=None):
    return {
        n["label"] for n in graph["nodes"] if type_ is None or n["type"] == type_
    }


def edge_pairs(graph, etype=None):
    labels = {n["id"]: n["label"] for n in graph["nodes"]}
    out = set()
    for e in graph["edges"]:
        if etype and e["type"] != etype:
            continue
        out.add((labels[e["s"]], labels[e["t"]], e["type"], e.get("label", "")))
    return out


def test_relation_from_holding_group():
    g = build_relation_graph(
        source_id="mock",
        target="星辰控股集团有限公司",
        depth=3,
        max_nodes=60,
        strategies=["legal", "shareholder", "invest", "phone"],
    )
    meta = g["meta"]
    assert meta["rootName"] == "星辰控股集团有限公司"
    stats = meta["stats"]

    companies = names_of(g, "co")
    # 集团 + 子公司群 + 对照样本均应收录
    assert {"星辰控股集团有限公司", "星辰置业有限公司", "星辰科技有限公司",
            "星辰供应链管理有限公司", "星辰数据服务有限公司", "远航物流有限公司",
            "远航仓储有限公司", "恒信贸易有限公司", "远港实业有限公司"} <= companies
    # 7 位自然人
    persons = names_of(g, "p")
    assert {"张伟明", "张丽", "李建国", "陈静", "王强", "刘洋", "赵大川"} <= persons

    # 关系类型齐备
    types = {e["type"] for e in g["edges"]}
    assert {"legal", "shareholder", "invest", "phone"} <= types

    pairs = edge_pairs(g)
    # 对外投资：控股 -> 置业/科技/供应链；科技 -> 数据服务；供应链/远航物流 -> 远航仓储
    assert ("星辰控股集团有限公司", "星辰科技有限公司", "invest", "星辰科技有限公司") in pairs
    assert ("星辰科技有限公司", "星辰数据服务有限公司", "invest", "星辰数据服务有限公司") in pairs
    assert ("远航物流有限公司", "远航仓储有限公司", "invest", "远航仓储有限公司") in pairs
    # 同电话：置业 <-> 远港实业
    phone_pairs = {(a, b) for a, b, t, _ in pairs if t == "phone"}
    assert ("星辰置业有限公司", "远港实业有限公司") in phone_pairs
    # 法人关系：控股 -- 张伟明
    assert ("星辰控股集团有限公司", "张伟明", "legal", "") in pairs
    # 股东比例标签
    assert ("星辰控股集团有限公司", "张丽", "shareholder", "40%") in pairs

    assert stats["companies"] >= 9
    assert stats["edges"] >= 15


def test_relation_depth_1_no_far_expansion():
    """深度 1 时只扩展目标企业一跳：不扩展再外层的邻居关系。"""
    g = build_relation_graph(
        source_id="mock",
        target="星辰科技有限公司",
        depth=1,
        max_nodes=40,
        strategies=["legal", "shareholder", "invest", "phone"],
    )
    companies = names_of(g, "co")
    pairs = edge_pairs(g)
    # 直接对外投资出现在图里（目标企业自身展开所得）
    assert "星辰科技有限公司" in companies
    assert "星辰数据服务有限公司" in companies
    assert ("星辰科技有限公司", "星辰数据服务有限公司", "invest", "星辰数据服务有限公司") in pairs
    # “远航物流 -> 远航仓储”需要先经 王强 两跳到达远航物流并展开它，
    # 深度 1 下远航物流节点不会被展开，因此该投资边不应出现
    assert ("远航物流有限公司", "远航仓储有限公司", "invest", "远航仓储有限公司") not in pairs


def test_relation_target_missing():
    with pytest.raises(ValueError):
        build_relation_graph("mock", "绝对不存在的公司XYZ", depth=2, max_nodes=20)


def test_relation_node_cap():
    g = build_relation_graph(
        source_id="mock", target="星辰控股集团有限公司",
        depth=3, max_nodes=12, strategies=["legal", "shareholder", "invest", "phone"],
    )
    assert len(g["nodes"]) <= 12
    assert g["meta"]["truncated"] is True
