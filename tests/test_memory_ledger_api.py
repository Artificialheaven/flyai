"""GET /api/my/memory/ledger：某张卡的剧情台账分组返回。"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api.routes_memory import memory_ledger
from flyai.config import AppConfig
from flyai.memory.ledger import LedgerItem
from flyai.user import UserScope


def _env(tmp_path, uid="u1"):
    scope = UserScope.build(uid, AppConfig(), tmp_path, None)
    state = SimpleNamespace(user=lambda _r: scope)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )
    return scope, request


def _ledger():
    return {
        "character": [],
        "item": [LedgerItem(kind="item", name="铁剑", state="随身佩戴",
                            status="active", updated_at="2026-10-11")],
        "location": [],
        "task": [
            LedgerItem(kind="task", name="已完成的委托", state="交付",
                       status="done", updated_at="2026-10-12"),
            LedgerItem(kind="task", name="寻找商队", state="追到黑森林",
                       status="open", updated_at="2026-10-16"),
        ],
        "foreshadow": [
            LedgerItem(kind="foreshadow", name="神秘信物", state="母亲遗物",
                       status="planted", updated_at="2026-10-09"),
        ],
    }


def test_ledger_grouped_by_kind(tmp_path):
    scope, req = _env(tmp_path)
    scope.char_memory.save_ledger("TA", _ledger())

    out = memory_ledger(req, char_id="TA")
    assert out["char_id"] == "TA"
    groups = {g["kind"]: g["items"] for g in out["groups"]}
    assert set(groups) == {"character", "item", "location", "task", "foreshadow"}
    assert [it["name"] for it in groups["item"]] == ["铁剑"]
    assert [it["name"] for it in groups["foreshadow"]] == ["神秘信物"]
    # 组内未终结在前（稳定排序：open 排在 done 之前）
    assert [it["name"] for it in groups["task"]] == ["寻找商队", "已完成的委托"]
    # 变迁史随条目返回
    assert groups["item"][0]["state"] == "随身佩戴"


def test_ledger_empty(tmp_path):
    _scope, req = _env(tmp_path)
    out = memory_ledger(req, char_id="NOPE")
    assert all(not g["items"] for g in out["groups"])


def test_ledger_requires_char_id(tmp_path):
    _scope, req = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        memory_ledger(req, char_id="  ")
    assert ei.value.status_code == 400
