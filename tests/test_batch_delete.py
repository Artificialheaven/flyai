"""批量删除会话接口单测。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_session import BatchDeleteBody, batch_delete_sessions
from flyai.sessions.json_store import JsonSessionStore


def _req(store):
    sc = SimpleNamespace(store=store)
    state = SimpleNamespace(user=lambda r: sc)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))


def test_batch_delete_deletes_existing(tmp_path):
    store = JsonSessionStore(tmp_path)
    s1 = store.create(title="一")
    s2 = store.create(title="二")
    s3 = store.create(title="三")

    r = batch_delete_sessions(BatchDeleteBody(ids=[s1.id, s3.id]), _req(store))
    assert r == {"ok": True, "deleted": 2}
    assert store.get(s1.id) is None
    assert store.get(s3.id) is None
    assert store.get(s2.id) is not None


def test_batch_delete_skips_missing_and_empty(tmp_path):
    store = JsonSessionStore(tmp_path)
    s1 = store.create()
    r = batch_delete_sessions(BatchDeleteBody(ids=[s1.id, "ghost-id"]), _req(store))
    assert r["deleted"] == 1
    # 空列表
    r0 = batch_delete_sessions(BatchDeleteBody(ids=[]), _req(store))
    assert r0["deleted"] == 0
