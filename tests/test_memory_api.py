"""记忆管理 API：列表/统计/日志/删除 + 鉴权（P6）。"""

import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_memory import (
    delete_entry,
    list_memory,
    memory_stats,
    recall_logs,
)
from flyai.config import AppConfig
from flyai.memory.logs import RecallLogBuffer
from flyai.user import UserScope


def _env(tmp_path, uid="u1"):
    scope = UserScope.build(uid, AppConfig(), tmp_path, None)
    state = SimpleNamespace(
        user=lambda _r: scope,
        recall_logs=RecallLogBuffer(),
    )

    def _user(_r):
        return scope

    state.user = _user
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )
    return scope, request, state


def test_list_empty(tmp_path):
    _scope, req, _s = _env(tmp_path)
    out = list_memory(req)
    assert out["entries"] == [] and out["total"] == 0


def test_list_filter_by_char_and_query(tmp_path):
    scope, req, _s = _env(tmp_path)
    scope.char_memory.append("TA", "用户特别怕水")
    scope.char_memory.append("TB", "另一个角色的事实")

    out = list_memory(req)
    assert out["total"] == 2

    only_ta = list_memory(req, char_id="TA")
    assert only_ta["total"] == 1
    assert only_ta["entries"][0]["text"] == "用户特别怕水"

    hit = list_memory(req, q="怕水")
    assert hit["total"] == 1
    miss = list_memory(req, q="无关内容")
    assert miss["total"] == 0


def test_stats(tmp_path):
    scope, req, _s = _env(tmp_path)
    scope.char_memory.append("TA", "一条事实")
    out = memory_stats(req)
    assert out["total"]["entries"] == 1
    assert out["total"]["active"] == 1
    assert len(out["per_char"]) == 1
    assert out["per_char"][0]["char_id"] == "TA"


def test_recall_logs_endpoint(tmp_path):
    scope, req, state = _env(tmp_path)
    state.recall_logs.add(kind="auto", topic="怕水", hits=2)
    out = recall_logs(req)
    assert len(out["logs"]) == 1
    assert out["logs"][0]["topic"] == "怕水"


def test_delete_entry(tmp_path):
    scope, req, _s = _env(tmp_path)
    scope.char_memory.append("TA", "要删掉的事实")
    entry_id = scope.char_memory.load_book("TA").entries[0].id

    out = delete_entry(req, entry_id)
    assert out["removed"] == 1
    assert scope.char_memory.load_book("TA").entries == []

    with pytest.raises(HTTPException) as ei:
        delete_entry(req, entry_id)
    assert ei.value.status_code == 404


def test_delete_entry_scoped_to_char(tmp_path):
    scope, req, _s = _env(tmp_path)
    scope.char_memory.append("TA", "同一文本")
    entry_id = scope.char_memory.load_book("TA").entries[0].id

    with pytest.raises(HTTPException) as ei:
        delete_entry(req, entry_id, char_id="TB")
    assert ei.value.status_code == 404
    assert scope.char_memory.load_book("TA").entries != []


def test_delete_entry_rejects_bad_id(tmp_path):
    _scope, req, _s = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        delete_entry(req, "../x")
    assert ei.value.status_code == 400


def test_requires_login(tmp_path):
    state = SimpleNamespace(
        user=lambda _r: (_ for _ in ()).throw(HTTPException(401, "未登录"))
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )
    with pytest.raises(HTTPException) as ei:
        list_memory(request)
    assert ei.value.status_code == 401
