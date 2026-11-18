"""用户邮箱字段：旧数据兼容、归一化、唯一性、按邮箱查找。"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.user import User, UserStore


def test_create_normalizes_email(tmp_path):
    store = UserStore(tmp_path)
    u = store.create("bob", "pw", email=" Bob@Example.COM ")
    assert u.email == "bob@example.com"
    assert store.get_by_email("BOB@example.com") is u
    # 持久化后仍然归一
    again = UserStore(tmp_path)
    assert again.get_by_name("bob").email == "bob@example.com"


def test_email_unique_case_insensitive(tmp_path):
    store = UserStore(tmp_path)
    store.create("bob", "pw", email="bob@example.com")
    with pytest.raises(ValueError) as ei:
        store.create("alice", "pw", email="BOB@example.com")
    assert "邮箱" in str(ei.value)


def test_empty_email_not_checked(tmp_path):
    store = UserStore(tmp_path)
    store.create("bob", "pw")
    store.create("alice", "pw")
    assert store.get_by_email("") is None


def test_legacy_users_json_without_email(tmp_path):
    (tmp_path / "users.json").write_text(
        json.dumps({"users": [{
            "id": "u1", "name": "old", "password_hash": "",
            "created_at": "2025-01-01T00:00:00", "level": 0,
            "quota_total": 0, "quota_used": 0,
        }]}),
        encoding="utf-8",
    )
    store = UserStore(tmp_path)
    u = store.get_by_name("old")
    assert u is not None and u.email == ""
    assert store.get_by_email("x@y.com") is None


def test_user_roundtrip_includes_email():
    u = User(name="bob", email="BOB@EXAMPLE.COM")
    d = u.to_dict()
    assert d["email"] == "bob@example.com"
    assert User.from_dict(d).email == "bob@example.com"
