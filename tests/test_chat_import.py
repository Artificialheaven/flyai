"""Chatbox 导出对话导入：备份 JSON / 单对话 Markdown → FlyAI 私聊房间会话。"""

import asyncio
import base64
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api.routes_session import import_chatbox
from flyai.sessions.chat_import import ChatImportError, parse_external_chats
from flyai.sessions.json_store import JsonSessionStore


# ---------- 解析单元测试 ----------

def test_backup_json_multiple_chats():
    backup = {
        "version": "1.0.0",
        "chats": [
            {
                "id": "c1", "title": "第一个对话",
                "createdAt": 1700000000000, "updatedAt": 1700000100000,
                "messages": [
                    {"role": "user", "content": "你好", "createdAt": 1700000000000},
                    {"role": "assistant", "content": "你好呀",
                     "reasoning": "用户在打招呼", "createdAt": 1700000100000},
                ],
            },
            {
                "id": "c2", "title": "第二个对话",
                "messages": [
                    {"role": "user", "content": "再说点啥"},
                    {"role": "assistant", "content": "好的"},
                ],
            },
        ],
        "config": {}, "prompts": [],
    }
    datas = parse_external_chats(json.dumps(backup).encode("utf-8"))
    assert len(datas) == 2

    d1 = datas[0]
    assert d1["session"]["title"] == "第一个对话"
    assert d1["session"]["created_at"].startswith("2023-")
    assert len(d1["members"]) == 2 and d1["members"][0]["is_user"]
    assert len(d1["threads"]) == 1
    t = d1["threads"][0]
    assert t["kind"] == "dm"
    assert t["member_ids"] == [d1["members"][1]["id"]]
    assert [m["role"] for m in t["messages"]] == ["user", "assistant"]
    assert t["messages"][1]["thinking"] == "用户在打招呼"

    assert datas[1]["session"]["title"] == "第二个对话"


def test_single_chat_object_and_message_array():
    single = {"title": "裸对话", "messages": [{"role": "user", "content": "hi"}]}
    datas = parse_external_chats(json.dumps(single).encode())
    assert len(datas) == 1 and datas[0]["session"]["title"] == "裸对话"

    arr = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    datas = parse_external_chats(json.dumps(arr).encode())
    assert len(datas) == 1
    msgs = datas[0]["threads"][0]["messages"]
    assert [m["content"] for m in msgs] == ["a", "b"]


def test_role_normalization_and_unknown_skipped():
    chat = {"title": "角色", "messages": [
        {"role": "developer", "content": "系统指令"},
        {"role": "weird", "content": "应丢弃"},
        {"role": "human", "content": "用户归一"},
        {"role": "model", "content": "助手归一"},
        {"role": "user", "content": "   "},  # 空消息丢弃
    ]}
    datas = parse_external_chats(json.dumps(chat).encode())
    roles = [m["role"] for m in datas[0]["threads"][0]["messages"]]
    assert roles == ["system", "user", "assistant"]


def test_multimodal_content_parts_and_images():
    png_b64 = base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"0" * 300).decode()
    chat = {
        "title": "图文",
        "messages": [
            {"role": "user",
             "content": [
                 {"type": "text", "text": "看这张图"},
                 {"type": "image_url",
                  "image_url": {"url": "data:image/png;base64,%s" % png_b64}},
             ]},
            {"role": "user", "content": "另一张",
             "images": ["data:image/jpeg;base64,%s" % png_b64]},
            {"role": "user", "content": "网络图片",
             "images": ["https://example.com/a.png"]},  # 外链丢弃
        ],
    }
    datas = parse_external_chats(json.dumps(chat).encode())
    msgs = datas[0]["threads"][0]["messages"]
    assert msgs[0]["content"] == "看这张图"
    assert msgs[0]["images"][0]["media_type"] == "image/png"
    assert msgs[0]["images"][0]["data"] == png_b64
    assert len(msgs[1]["images"]) == 1
    assert msgs[2]["images"] is None


def test_markdown_standard_headings():
    md = (
        "# 我的对话\n\n"
        "> Exported by Chatbox\n\n"
        "## 🧑 User\n\n你好啊\n\n"
        "## 🤖 Assistant\n\n你好，很高兴见到你\n"
    )
    datas = parse_external_chats(md.encode())
    assert len(datas) == 1
    assert datas[0]["session"]["title"] == "我的对话"
    msgs = datas[0]["threads"][0]["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user" and msgs[0]["content"] == "你好啊"
    assert msgs[1]["role"] == "assistant" and msgs[1]["content"] == "你好，很高兴见到你"


def test_markdown_bold_labels_and_chinese():
    md = (
        "**User**\n\n在吗\n\n"
        "**Assistant:**\n\n在的\n\n"
        "### 系统\n\n系统提示文本\n"
    )
    datas = parse_external_chats(md.encode())
    msgs = datas[0]["threads"][0]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "system"]
    assert msgs[2]["content"] == "系统提示文本"


def test_markdown_multiline_body_preserved():
    md = "## User\n\n第一行\n第二行\n\n## Assistant\n\n回复"
    datas = parse_external_chats(md.encode())
    msgs = datas[0]["threads"][0]["messages"]
    assert msgs[0]["content"] == "第一行\n第二行"


def test_markdown_chatbox_fenced_export():
    """Chatbox 真实导出：H1 标题 + ## N. 话题 + **role**: 标签 + 围栏正文 + HTML 页脚。"""
    md = (
        "# 基础\n\n"
        "## 1. 退学少女的金钱交易\n\n"
        "**system**: \n\n"
        "```\n"
        "【系统限制】\n"
        "1. 禁止暴露AI身份\n"
        "```\n\n"
        "**user**: \n\n"
        "```\n"
        "你好~\n"
        "第二行内容\n"
        "```\n\n"
        "**assistant**: \n\n"
        "```\n"
        "你好呀\\~\n"
        "```\n\n"
        "--------------------\n\n\n"
        '<a href="https://chatboxai.app">\n'
        "<b>Chatbox AI</b>\n"
        "</a>\n"
    )
    datas = parse_external_chats(md.encode())
    assert len(datas) == 1
    assert datas[0]["session"]["title"] == "基础"
    msgs = datas[0]["threads"][0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
    assert msgs[0]["content"] == "【系统限制】\n1. 禁止暴露AI身份"
    assert msgs[1]["content"] == "你好~\n第二行内容"
    assert msgs[2]["content"] == r"你好呀\~"
    # HTML 页脚没有被拼进最后一条消息
    assert "chatboxai.app" not in msgs[2]["content"]


def test_markdown_chatbox_fence_label_inside_body():
    """围栏正文中出现标签模样的行也必须原样保留。"""
    md = (
        "**user**: \n\n```\n"
        "我说 **assistant**: 这不是标签\n"
        "```\n\n"
        "**assistant**: \n\n```\n嗯\n```\n"
    )
    datas = parse_external_chats(md.encode())
    msgs = datas[0]["threads"][0]["messages"]
    assert len(msgs) == 2
    assert msgs[0]["content"] == "我说 **assistant**: 这不是标签"


def test_rejects_bad_files():
    with pytest.raises(ChatImportError):
        parse_external_chats(b"")
    with pytest.raises(ChatImportError):
        parse_external_chats(b"{" * 4)  # 非法 JSON
    with pytest.raises(ChatImportError):
        parse_external_chats(json.dumps({"config": {}}).encode())  # 没有对话
    with pytest.raises(ChatImportError):
        parse_external_chats("# 标题\n\n没有任何角色段的纯文本".encode())
    # 自家导出包走错入口
    with pytest.raises(ChatImportError):
        parse_external_chats(json.dumps(
            {"type": "flyai-conversation", "version": 2, "data": {}}).encode())


# ---------- 路由测试 ----------

class _FakeLore:
    def get_character(self, cid):
        return None

    def get_world(self, wid):
        return None


class _FakeUpload:
    def __init__(self, raw):
        self._raw = raw

    async def read(self):
        return self._raw


def _request(sc):
    state = SimpleNamespace()
    state.user = lambda request: sc
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_route_imports_single_backup_chat(tmp_path):
    store = JsonSessionStore(tmp_path / "s")
    sc = SimpleNamespace(uid="u1", level=1, store=store, lore=_FakeLore())
    backup = {"chats": [{"title": "路由对话", "messages": [
        {"role": "user", "content": "1"},
        {"role": "assistant", "content": "2"},
    ]}]}
    out = _run(import_chatbox(_request(sc),
                              _FakeUpload(json.dumps(backup).encode())))

    assert out["title"] == "路由对话"
    assert out["import_summary"] == {"conversations": 1, "messages": 2, "images": 0}
    restored = store.get(out["id"])
    assert restored is not None
    assert restored.threads[0].messages[0].content == "1"
    assert restored.threads[0].member_ids == [m.id for m in restored.members if not m.is_user]


def test_route_imports_multiple_chats_and_markdown(tmp_path):
    store = JsonSessionStore(tmp_path / "s")
    sc = SimpleNamespace(uid="u1", level=1, store=store, lore=_FakeLore())
    backup = {"chats": [
        {"title": "A", "messages": [{"role": "user", "content": "a"}]},
        {"title": "B", "messages": [{"role": "assistant", "content": "b"}]},
    ]}
    out = _run(import_chatbox(_request(sc),
                              _FakeUpload(json.dumps(backup).encode())))
    assert "sessions" in out and len(out["sessions"]) == 2
    assert out["import_summary"]["conversations"] == 2
    assert len(store.list()) == 2

    md = "# MD\n\n## User\n\n嗨\n\n## Assistant\n\n嗯\n".encode()
    out2 = _run(import_chatbox(_request(sc), _FakeUpload(md)))
    assert "sessions" not in out2
    assert out2["import_summary"]["messages"] == 2
    assert len(store.list()) == 3


def test_route_rejects_unrecognized(tmp_path):
    store = JsonSessionStore(tmp_path / "s")
    sc = SimpleNamespace(uid="u1", level=1, store=store, lore=_FakeLore())
    with pytest.raises(HTTPException) as ei:
        _run(import_chatbox(_request(sc), _FakeUpload(b"not a chat export")))
    assert ei.value.status_code == 400
