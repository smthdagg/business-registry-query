"""演示数据源（mock）搜索行为测试。"""

import pytest

from app.sources import get_source


@pytest.fixture(scope="module")
def mock():
    return get_source("mock")


def test_angles_supported(mock):
    assert "法人" in mock.angles
    assert "股东" in mock.angles
    assert "信用代码" in mock.angles
    assert "电话" in mock.angles


def test_search_by_company_name(mock):
    hits = mock.search("星辰控股集团", angle="企业名")
    assert hits and hits[0]["name"] == "星辰控股集团有限公司"


def test_search_by_person_legal(mock):
    hits = mock.search("张伟明", angle="法人")
    names = {h["name"] for h in hits}
    # 张伟明是星辰控股、星辰置业的法定代表人
    assert names == {"星辰控股集团有限公司", "星辰置业有限公司"}


def test_search_by_shareholder(mock):
    hits = mock.search("王强", angle="股东")
    names = {h["name"] for h in hits}
    # 王强出现在星辰科技 / 星辰数据 / 远航物流 / 远航仓储的股东列表
    assert {"星辰科技有限公司", "远航物流有限公司"} <= names
    assert "星辰置业有限公司" not in names  # 王强非其股东


def test_search_credit_code_exact(mock):
    hits = mock.search("913101156811234568", angle="信用代码")
    assert len(hits) == 1
    assert hits[0]["name"] == "星辰置业有限公司"


def test_search_by_phone_shared(mock):
    # 星辰置业与远港实业共用一个电话号码 -> 应命中 2 家
    hits = mock.search("021-60001002", angle="电话")
    names = {h["name"] for h in hits}
    assert names == {"星辰置业有限公司", "远港实业有限公司"}


def test_search_no_hit(mock):
    assert mock.search("不存在的公司AAA", angle="企业名") == []
    assert mock.search("", angle="综合") == []


def test_search_keyword_phone_too_short(mock):
    assert mock.search("02", angle="电话") == []
