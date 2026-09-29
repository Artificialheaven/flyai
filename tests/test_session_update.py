"""会话 PATCH：重命名 / 置顶。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.api.routes_session import SessionUpdate, update_session
from flyai.sessions.json_store import JsonSessionStore


def _req(store):
    sc = SimpleNamespace(store=store)
    state = SimpleNamespace(user=lambda r: sc)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))


def test_rename_persists_and_trims(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="旧标题")
    r = update_session(s.id, SessionUpdate(title="  新名字  "), _req(store))
    assert r["title"] == "新名字"
    assert store.get(s.id).title == "新名字"


def test_rename_blank_keeps_old_title(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="保留我")
    update_session(s.id, SessionUpdate(title="   "), _req(store))
    assert store.get(s.id).title == "保留我"


def test_toggle_pin_persists(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="会话")
    r = update_session(s.id, SessionUpdate(pinned=True), _req(store))
    assert r["pinned"] is True
    assert store.get(s.id).pinned is True
    update_session(s.id, SessionUpdate(pinned=False), _req(store))
    assert store.get(s.id).pinned is False


def test_update_missing_session_404(tmp_path):
    store = JsonSessionStore(tmp_path)
    with pytest.raises(Exception) as exc:
        update_session("ghost", SessionUpdate(title="x"), _req(store))
    assert getattr(exc.value, "status_code", None) == 404
