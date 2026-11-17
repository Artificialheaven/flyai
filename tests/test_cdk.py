"""CDK 兑换码：生成/导入去重、有效期、兑换次数、每用户一次、停用。"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.cdk import CdkError, CdkStore
from flyai.user import UserStore


def _store(tmp_path):
    return CdkStore(tmp_path)


def test_generate_creates_usable_codes(tmp_path):
    store = _store(tmp_path)
    out = store.generate(3, amount=100, max_uses=1,
                         expires_at="2099-12-31")
    assert len(out) == 3
    for view in out:
        assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}",
                            view["code"])
        assert "0" not in view["code"] and "O" not in view["code"]
        assert view["amount"] == 100 and view["max_uses"] == 1
        assert view["status"] == "usable" and view["used"] == 0
    # 持久化后重新加载仍存在
    reloaded = _store(tmp_path)
    assert len(reloaded.list()) == 3


def test_import_dedupes_and_skips_invalid(tmp_path):
    store = _store(tmp_path)
    result = store.import_codes(
        "abc-123\nABC-123\nxy\n", amount=5, max_uses=0)
    assert result["added"] == ["ABC-123"]
    assert result["duplicates"] == ["ABC-123"]
    assert result["invalid"] == ["xy"]
    assert store.list()[0]["amount"] == 5


def test_expired_and_disabled_rejected(tmp_path):
    store = _store(tmp_path)
    expired = store.generate(1, 10, 1, "2020-01-01")[0]["code"]
    with pytest.raises(CdkError) as ei:
        store.check_redeem("u1", expired)
    assert "过期" in ei.value.message

    fresh = store.generate(1, 10, 1, "")[0]["code"]
    store.disable(fresh)
    with pytest.raises(CdkError) as ei:
        store.check_redeem("u1", fresh)
    assert "停用" in ei.value.message


def test_max_uses_and_single_use_per_user(tmp_path):
    store = _store(tmp_path)
    code = store.generate(1, 10, max_uses=1)[0]["code"]
    info = store.check_redeem("u1", code)
    store.mark_redeemed(info["key"], "u1")

    # 同一用户再兑：即使次数未耗尽也拒绝（每用户一次）
    with pytest.raises(CdkError) as ei:
        store.check_redeem("u1", code)
    assert "兑换过" in ei.value.message
    # 全站次数已用完
    with pytest.raises(CdkError) as ei:
        store.check_redeem("u2", code)
    assert "次数" in ei.value.message


def test_unlimited_uses_allow_many_users(tmp_path):
    store = _store(tmp_path)
    code = store.generate(1, 10, max_uses=0)[0]["code"]
    for uid in ("u1", "u2", "u3"):
        info = store.check_redeem(uid, code)
        store.mark_redeemed(info["key"], uid)
    view = store.list()[0]
    assert view["used"] == 3 and view["status"] == "usable"


def test_code_normalization_and_extract(tmp_path):
    store = _store(tmp_path)
    code = store.generate(1, 10, 1)[0]["code"]
    # 大小写与去连字符都能识别
    assert store.check_redeem("u1", code.lower())["key"] == code
    assert store.check_redeem("u1", code.replace("-", ""))["key"] == code

    extracted = store.extract()
    assert extracted["code"] == code and extracted["status"] == "usable"
    # 取出不删除
    assert len(store.list()) == 1


def test_redeem_adds_quota_and_rollback(tmp_path):
    store = _store(tmp_path)
    users = UserStore(tmp_path)
    u = users.create("bob", "", level=0)
    users.set_quota_total(u.id, 10)
    code = store.generate(1, 25, 1)[0]["code"]

    info = store.check_redeem(u.id, code)
    store.mark_redeemed(info["key"], u.id)
    assert users.add_quota(u.id, info["amount"]) == pytest.approx(35)
    assert users.get_by_id(u.id).quota_total == pytest.approx(35)

    # 模拟落库失败后的回滚：兑换记录撤销，可重新兑换
    store.unmark_redeemed(info["key"], u.id)
    assert store.check_redeem(u.id, code)["amount"] == 25


def test_export_contains_header_and_rows(tmp_path):
    store = _store(tmp_path)
    store.generate(2, 10, 1, "2099-12-31")
    text = store.export()
    lines = text.splitlines()
    assert lines[0] == "代码,额度,可兑换次数,已兑换,有效期,状态"
    assert len(lines) == 3