"""公司档案（profile）：风鸟结构化解析 + 跨源合并的单元测试。"""

from app.services.profile import _rb_basic_from_jbxx, _rb_branches, _rb_invest, _rb_shareholders


def test_rb_basic_from_jbxx():
    jb = {
        "entName": "华为技术有限公司",
        "personName": "赵明路",
        "entStatus": "在营",
        "uniscid": "914403001922038216",
        "esDate": "1987-09-15",
        "yrAddress": "深圳市龙岗区坂田华为总部办公楼",
        "opScope": "一般经营项目：程控交换机…",
        "telList": ["0755-28780808"],
    }
    basic = _rb_basic_from_jbxx(jb)
    assert basic["name"] == "华为技术有限公司"
    assert basic["legalPersonName"] == "赵明路"
    assert basic["regStatus"] == "在营"
    assert basic["creditCode"] == "914403001922038216"
    assert basic["estiblishTime"] == "1987-09-15"
    assert "0755-28780808" in basic["phone"]
    assert "程控交换机" in basic["businessScope"]


def test_rb_shareholders_person_vs_entity():
    items = [
        {"shaName": "华为投资控股有限公司", "fundedRatio": "100%", "shaId": "S1"},
        {"shaName": "张三", "fundedRatio": "3.2%", "subConAm": "350万", "shaId": "S2"},
    ]
    out = _rb_shareholders(items)
    assert out[0]["type"] == "entity" and out[0]["name"] == "华为投资控股有限公司"
    assert out[1]["type"] == "person" and out[1]["percent"] == "3.2%"
    assert out[1]["subscribed"] == "350万"


def test_rb_invest_and_branches():
    invest = _rb_invest([
        {"entName": "华为云计算技术有限公司", "funderRatio": "100%", "entStatus": "存续", "entid": "E1"},
        {"entName": "", "entid": "E2"},
    ])
    assert invest == [{"name": "华为云计算技术有限公司", "percent": "100%", "status": "存续", "pid": "E1"}]

    branches = _rb_branches([
        {"brName": "华为技术有限公司东莞分公司", "brPrincipal": "童国凡", "entStatus": "在营", "entid": "B1"},
    ])
    assert branches[0]["name"] == "华为技术有限公司东莞分公司"
    assert branches[0]["principal"] == "童国凡"
