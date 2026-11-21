# -*- coding: utf-8 -*-
"""会话列表摘要缓存：按 (mtime_ns, size) 复用解析、写删后失效、坏文件只试一次、并发安全。"""
from __future__ import annotations

import json as json_mod
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import Session


@pytest.fixture()
def store(tmp_path):
    return JsonSessionStore(tmp_path / "sessions")


@pytest.fixture()
def parses(monkeypatch):
    """统计 JSON 解析次数（json.load 内部同样走 json.loads）。"""
    calls = {"n": 0}
    real = json_mod.loads

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(json_mod, "loads", counting)
    return calls


def test_list_parses_each_file_once(store, parses):
    store.save(Session(title="A"))
    store.save(Session(title="B"))
    first = store.list()
    assert {s.title for s in first} == {"A", "B"}
    assert parses["n"] >= 2

    before = parses["n"]
    second = store.list()
    assert [s.id for s in second] == [s.id for s in first]
    # 第二次列表只 stat 不解析
    assert parses["n"] == before


def test_save_refreshes_summary(store, parses):
    s = Session(title="old")
    store.save(s)
    assert [x.title for x in store.list()] == ["old"]

    before = parses["n"]
    s.title = "改名之后更长的标题"
    store.save(s)
    assert [x.title for x in store.list()] == ["改名之后更长的标题"]
    assert parses["n"] > before


def test_get_not_cached_reads_latest(store, parses):
    s = Session(title="v1")
    store.save(s)
    store.list()
    before = parses["n"]
    assert store.get(s.id).title == "v1"
    # get() 不走列表缓存，始终读盘（save_merged 依赖最新正文）
    assert parses["n"] > before


def test_message_count_in_summary(store):
    s = Session(
        title="m",
        messages=[
            Message(role="user", content="hi"),
            Message(role="assistant", content="yo"),
        ],
    )
    store.save(s)
    listed = store.list()
    assert len(listed) == 1 and listed[0].message_count == 2


def test_bad_json_hidden_and_tried_once(store, parses):
    store.save(Session(title="good"))
    (store._dir / "bad.json").write_text("{不是 json", encoding="utf-8")
    assert [s.title for s in store.list()] == ["good"]

    before = parses["n"]
    assert [s.title for s in store.list()] == ["good"]
    # 坏文件也写了缓存哨兵，第二次不再重复解析
    assert parses["n"] == before


def test_delete_prunes_cache(store):
    s = Session(title="x")
    store.save(s)
    store.list()
    key = str(store._path(s.id))
    assert key in store._summary_cache

    store.delete(s.id)
    assert key not in store._summary_cache
    assert store.list() == []


def test_list_prunes_files_removed_outside_store(store):
    s = Session(title="x")
    store.save(s)
    store.list()
    store._path(s.id).unlink()
    assert store.list() == []
    assert store._summary_cache == {}


def test_concurrent_list_is_consistent(store):
    for i in range(6):
        store.save(Session(title="s{}".format(i)))

    def run(_):
        return [(s.id, s.title, s.message_count) for s in store.list()]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, range(24)))

    assert all(r == results[0] for r in results)
    assert len(results[0]) == 6
