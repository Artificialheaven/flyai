"""邀请码存储：生成/重新生成、次数、归一化匹配、自定义码、持久化。"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.invites import InviteError, InviteStore


def _store(tmp_path):
    return InviteStore(tmp_path)


def test_generate_default_five_and_persist(tmp_path):
    store = _store(tmp_path)
    view = store.generate("u1")
    assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", view["code"])
    assert "0" not in view["code"] and "O" not in view["code"]
    assert view["max_uses"] == 5 and view["used"] == 0
    assert view["remaining"] == 5 and view["status"] == "usable"
    assert store.get_for_owner("u1")["code"] == view["code"]

    reloaded = _store(tmp_path)
    assert reloaded.get_for_owner("u1")["code"] == view["code"]


def test_exhausted_after_max_uses(tmp_path):
    store = _store(tmp_path)
    code = store.generate("u1", max_uses=2)["code"]
    store.mark_used(code, "n1", "alice")
    store.mark_used(code, "n2", "bob")
    view = store.check(code)
    assert view["used"] == 2 and view["remaining"] == 0
    assert view["status"] == "exhausted"
    with pytest.raises(InviteError) as ei:
        store.mark_used(code, "n3", "carol")
    assert ei.value.message == "邀请码已用尽"


def test_regenerate_invalidates_old(tmp_path):
    store = _store(tmp_path)
    old = store.generate("u1")["code"]
    new = store.generate("u1")["code"]
    assert new != old
    assert store.check(old) is None
    # 同一 owner 只有一个有效码
    assert len([v for v in store.list_all() if v["owner"] == "u1"]) == 1


def test_code_normalization(tmp_path):
    store = _store(tmp_path)
    code = store.generate("u1")["code"]
    assert store.check(code.lower())["code"] == code
    assert store.check(code.replace("-", ""))["code"] == code
    assert store.mark_used(code.lower(), "n1", "alice")["used"] == 1


def test_unknown_code_returns_none_and_mark_raises(tmp_path):
    store = _store(tmp_path)
    assert store.check("NOPE-NOPE-NOPE") is None
    with pytest.raises(InviteError) as ei:
        store.mark_used("NOPE-NOPE-NOPE", "n1", "alice")
    assert ei.value.message == "邀请码无效"


@pytest.mark.parametrize("code", ["abc", "a b c d", "-ABCD", "ABCD-", "ab@cd"])
def test_custom_code_validation(tmp_path, code):
    store = _store(tmp_path)
    with pytest.raises(InviteError) as ei:
        store.set_custom("u1", code, 5)
    assert ei.value.message == "邀请码格式不正确"


def test_custom_code_is_uppercased(tmp_path):
    store = _store(tmp_path)
    view = store.set_custom("u1", " my-code ", 3)
    assert view["code"] == "MY-CODE"


def test_custom_code_conflict_other_owner(tmp_path):
    store = _store(tmp_path)
    store.set_custom("u1", "ABCD-EFGH", 5)
    with pytest.raises(InviteError) as ei:
        store.set_custom("u2", "abcdefgh", 5)
    assert ei.value.message == "邀请码已被使用"
    with pytest.raises(InviteError) as ei:
        store.set_custom("u2", "ABCD-EFGH", 5)
    assert ei.value.message == "邀请码已被使用"


def test_custom_same_code_updates_max_uses_keeps_uses(tmp_path):
    store = _store(tmp_path)
    store.set_custom("u1", "MY-CODE", 5)
    store.mark_used("MY-CODE", "n1", "alice")
    view = store.set_custom("u1", "my-code", 2)
    assert view["used"] == 1 and view["max_uses"] == 2
    assert view["uses"][0]["name"] == "alice"
    with pytest.raises(InviteError) as ei:
        store.set_custom("u1", "MY-CODE", 0)
    assert ei.value.message == "邀请码人数上限必须为 1-10000 的整数"


def test_custom_max_uses_cannot_drop_below_used(tmp_path):
    store = _store(tmp_path)
    store.set_custom("u1", "MY-CODE", 5)
    store.mark_used("MY-CODE", "n1", "alice")
    store.mark_used("MY-CODE", "n2", "bob")
    with pytest.raises(InviteError) as ei:
        store.set_custom("u1", "MY-CODE", 1)
    assert ei.value.message == "邀请码使用人数不能少于已邀请人数"


def test_custom_different_code_resets_uses(tmp_path):
    store = _store(tmp_path)
    store.set_custom("u1", "OLD-CODE", 5)
    store.mark_used("OLD-CODE", "n1", "alice")
    view = store.set_custom("u1", "NEW-CODE", 5)
    assert view["used"] == 0 and store.check("OLD-CODE") is None


@pytest.mark.parametrize("bad", [0, -1, 10001, "abc"])
def test_max_uses_bounds(tmp_path, bad):
    store = _store(tmp_path)
    with pytest.raises(InviteError) as ei:
        store.generate("u1", bad)
    assert ei.value.message == "邀请码人数上限必须为 1-10000 的整数"


def test_unmark_used_and_remove(tmp_path):
    store = _store(tmp_path)
    code = store.generate("u1")["code"]
    store.mark_used(code, "n1", "alice")
    store.unmark_used(code, "n1")
    assert store.check(code)["used"] == 0

    assert store.remove(code) is True
    assert store.remove(code) is False
    assert store.get_for_owner("u1") is None

    code2 = store.generate("u2")["code"]
    assert store.remove_owner("u2") is True
    assert store.remove_owner("u2") is False
    assert store.check(code2) is None


def test_load_dedupes_owner_by_newest(tmp_path):
    import json

    (tmp_path / "invites.json").write_text(json.dumps({
        "OLD-CODE": {"code": "OLD-CODE", "owner": "u1", "max_uses": 5,
                     "created_at": 1, "uses": []},
        "NEW-CODE": {"code": "NEW-CODE", "owner": "u1", "max_uses": 5,
                     "created_at": 2, "uses": []},
    }), encoding="utf-8")
    store = _store(tmp_path)
    assert store.get_for_owner("u1")["code"] == "NEW-CODE"
    assert store.check("OLD-CODE") is None
