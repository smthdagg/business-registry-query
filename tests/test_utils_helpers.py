"""纯函数辅助：电话严格相等 / 人-企关联判定（关联精度修复的核心）。"""

from app.utils import phones_equal, record_person_hit


def test_phones_equal_basic():
    assert phones_equal("021-60001002", "02160001002") is True
    assert phones_equal("010-88000001", "01088000001") is True
    assert phones_equal("12345678", "12345678") is True


def test_phones_equal_mask_rejected():
    # 天眼查脱敏号码（带 *）不可信，应当全部拒绝，避免误关联
    assert phones_equal("0108306****", "0108306****") is False
    assert phones_equal("010-83064986", "0108306****") is False
    assert phones_equal("0108306", "0108306****") is False
    assert phones_equal("", "0108306") is False
    assert phones_equal(None, "0108306") is False


def test_record_person_hit_roles():
    rec = {
        "legalPersonName": "张伟明",
        "shareholders": [{"name": "张丽", "percent": "40%"}],
        "matchType": "公司名称匹配",
    }
    assert record_person_hit(rec, "张伟明") is True   # 法人
    assert record_person_hit(rec, "张丽") is True     # 股东
    assert record_person_hit(rec, "张三") is False    # 无关人


def test_record_person_hit_company_name_noise():
    # 公司名含“张伟明”但没任职/持股关系，不得算关联
    rec = {
        "name": "张伟明科技有限公司",
        "legalPersonName": "王五",
        "matchType": "公司名称匹配",
    }
    assert record_person_hit(rec, "张伟明") is False


def test_record_person_hit_person_rows():
    # 查老板的人员条目（type=person）必须原样通过过滤，不能被当成无关记录丢弃
    assert record_person_hit({"type": "person", "name": "苏胜"}, "苏胜") is True
    assert record_person_hit({"type": "person", "name": "苏胜"}, "苏建军") is False
    assert record_person_hit({"type": "person", "name": ""}, "苏胜") is False


def test_record_person_hit_match_type():
    rec = {"legalPersonName": "李四", "matchType": "法定代表人匹配"}
    assert record_person_hit(rec, "李四") is True
    rec2 = {"legalPersonName": "李四", "matchType": "股东匹配"}
    assert record_person_hit(rec2, "李四") is True
    assert record_person_hit({}, "某甲") is False