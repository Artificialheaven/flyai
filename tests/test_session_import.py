"""会话导入：flyai-conversation v2 / v3(zip) 导出包 → 全新会话；卡复用/降级副本策略。"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai import transfer
from flyai.api.routes_session import (
    SessionImportPayload,
    import_session,
    import_session_zip,
    session_export,
    session_export_zip,
)
from flyai.lore.models import Character, World
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread


class _FakeLore:
    def __init__(self, chars=None, worlds=None):
        self._chars = {c.id: c for c in (chars or [])}
        self._worlds = {w.id: w for w in (worlds or [])}

    def get_character(self, cid):
        return self._chars.get(cid)

    def get_world(self, wid):
        return self._worlds.get(wid)


def _env(tmp_path, chars_in_library):
    store = JsonSessionStore(tmp_path / "s")
    s = Session(title="原房间")
    world = World(id="w1", name="测试世界")
    s.world_id = world.id
    s.members.append(RoomMember(name="你", is_user=True))
    chars = []
    for i in range(2):
        cid = "c%d" % (i + 1)
        ch = Character(id=cid, name="角色%d" % (i + 1))
        chars.append(ch)
        m = RoomMember(name=ch.name, character_id=cid)
        s.members.append(m)
        t = Thread(kind="dm", title=m.name, member_ids=[m.id])
        from flyai.schemas import Message
        t.messages.append(Message(role="user", content="你好%d" % i))
        t.messages.append(Message(role="assistant", content="回复%d" % i))
        s.threads.append(t)
    ais = list(s.members[1:])
    s.threads.append(Thread(kind="group", title="群聊",
                            member_ids=[m.id for m in ais]))
    store.save(s)

    # 导出端的 lore 永远含全部卡；导入端的 lore 按用例决定
    export_lore = _FakeLore(chars, [world])
    sc = SimpleNamespace(uid="admin", level=256, store=store, lore=export_lore)
    state = SimpleNamespace()
    state.user = lambda request: sc
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))

    resp = session_export(s.id, request)
    envelope = json.loads(resp.body)

    # 切换为导入环境（新 store，lore 按用例）
    new_store = JsonSessionStore(tmp_path / "s2")
    imp_lore = _FakeLore(
        chars if chars_in_library else [],
        [world] if chars_in_library else [],
    )
    sc2 = SimpleNamespace(uid="admin", level=256, store=new_store, lore=imp_lore)
    state2 = SimpleNamespace()
    state2.user = lambda request: sc2
    request2 = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state2)))
    return envelope, new_store, request2


def _call(envelope, request):
    payload = SessionImportPayload(**envelope)
    return import_session(payload, request)


def test_import_reuses_library_cards_and_world(tmp_path):
    envelope, store, request = _env(tmp_path, chars_in_library=True)
    out = _call(envelope, request)

    assert out["id"] != envelope["data"]["session"]["id"]  # 新会话 id
    assert out["import_summary"] == {"reused_cards": 2, "scoped_cards": 0}
    assert out["world_id"] == "w1"
    assert not out["pinned"]

    s2 = store.get(out["id"])
    assert s2.title == "原房间"
    assert len(s2.threads) == 3
    dm = s2.threads[0]
    assert [m.role for m in dm.messages] == ["user", "assistant"]
    assert dm.messages[0].content == "你好0"
    # 成员保持库引用，无副本
    ai = [m for m in s2.members if not m.is_user]
    assert all(m.character_id and not m.override for m in ai)


def test_import_missing_cards_become_scoped(tmp_path):
    envelope, store, request = _env(tmp_path, chars_in_library=False)
    out = _call(envelope, request)

    assert out["import_summary"] == {"reused_cards": 0, "scoped_cards": 2}
    s2 = store.get(out["id"])
    ai = [m for m in s2.members if not m.is_user]
    assert all(not m.character_id and m.override for m in ai)
    # 副本卡内容就是随包卡；导出用的 ref 字段不应落进副本
    assert ai[0].override["id"] in ("c1", "c2")
    assert "ref" not in ai[0].override
    # 世界同样降为会话副本
    assert not s2.world_id and s2.world_override
    # 窗口仍能按成员 id 关联（成员保留原 id）
    assert s2.threads[-1].member_ids == [m.id for m in ai]


def test_import_twice_creates_two_sessions(tmp_path):
    envelope, store, request = _env(tmp_path, chars_in_library=False)
    a = _call(envelope, request)
    b = _call(envelope, request)
    assert a["id"] != b["id"]
    assert len(store.list()) == 2  # 没有互相覆盖


def test_import_rejects_bad_type_and_version(tmp_path):
    envelope, store, request = _env(tmp_path, chars_in_library=False)
    bad = dict(envelope)
    bad["type"] = "other"
    with pytest.raises(HTTPException) as ei:
        _call(bad, request)
    assert ei.value.status_code == 400

    bad = dict(envelope)
    bad["version"] = 1
    with pytest.raises(HTTPException) as ek:
        _call(bad, request)
    assert ek.value.status_code == 400


class _FakeUpload:
    """模拟 fastapi.UploadFile：只需要 await read()。"""

    def __init__(self, raw):
        self._raw = raw

    async def read(self):
        return self._raw


def _zip_env(tmp_path, uid="admin"):
    """带素材的 galgame 会话：背景 PNG + 一条 TTS 语音，消息中引用背景 URL。"""
    store = JsonSessionStore(tmp_path / ("store-%s" % uid))
    upload_dir = tmp_path / ("up-%s" % uid)

    s = Session(title="Galgame房", mode="galgame")
    s.members.append(RoomMember(name="你", is_user=True))
    ch = Character(id="c1", name="女主")
    m = RoomMember(name="女主", character_id="c1")
    s.members.append(m)

    bg_rel = "galgame/backgrounds/%s/bg.png" % s.id
    tts_rel = "tts_1.mp3"
    bg_url = "/uploads/%s/%s" % (uid, bg_rel)
    tts_url = "/uploads/%s/%s" % (uid, tts_rel)
    _write_upload(upload_dir, bg_rel, b"BGPNG")
    _write_upload(upload_dir, tts_rel, b"MP3")

    t = Thread(kind="dm", title="女主", member_ids=[m.id])
    t.messages.append(Message(role="assistant", content="这里是 " + bg_url))
    s.threads.append(t)
    s.galgame = {"background": {"id": "a1", "url": bg_url},
                 "sprite": {}, "music": {}}
    s.galgame_tts = {"你好呀": tts_url}
    store.save(s)

    lore = _FakeLore([ch])
    sc = SimpleNamespace(uid=uid, level=1, store=store, lore=lore,
                         upload_dir=str(upload_dir))
    request = _request(sc)
    return s, request, upload_dir


def _write_upload(upload_dir, rel, data):
    from pathlib import Path
    target = Path(upload_dir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


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


def test_export_zip_bundles_galgame_assets(tmp_path):
    s, request, upload_dir = _zip_env(tmp_path)
    resp = session_export_zip(s.id, request)
    assert resp.media_type == "application/zip"

    text, entries = transfer.open_export_zip(resp.body)
    rels = {e["rel"] for e in entries}
    assert any(rel.endswith("bg.png") for rel in rels)
    assert "tts_1.mp3" in rels

    envelope = json.loads(text)
    data = envelope["data"]
    assert data["session"]["mode"] == "galgame"
    assert data["galgame"]["background"]["url"].endswith("bg.png")
    assert data["galgame_tts"] == {"你好呀": "/uploads/admin/tts_1.mp3"}


def test_export_zip_without_tts_drops_voice(tmp_path):
    s, request, upload_dir = _zip_env(tmp_path)
    resp = session_export_zip(s.id, request, include_tts=False)
    text, entries = transfer.open_export_zip(resp.body)
    assert "tts_1.mp3" not in {e["rel"] for e in entries}
    data = json.loads(text)["data"]
    assert data["galgame_tts"] == {}


def test_import_zip_restores_session_and_rewrites_urls(tmp_path):
    s, request, _ = _zip_env(tmp_path)
    raw = session_export_zip(s.id, request).body

    # 导入端：新账号 bob、空 store、空素材目录、空卡库
    imp_store = JsonSessionStore(tmp_path / "store-bob-imp")
    imp_up = tmp_path / "up-bob-imp"
    imp_up.mkdir()
    sc = SimpleNamespace(uid="bob", level=1, store=imp_store,
                         lore=_FakeLore([]), upload_dir=str(imp_up))
    request2 = _request(sc)

    out = _run(import_session_zip(request2, _FakeUpload(raw)))

    assert out["mode"] == "galgame"
    assert out["import_summary"]["assets"] == 2
    new_bg = out["galgame"]["background"]["url"]
    assert new_bg.startswith("/uploads/bob/galgame/backgrounds/")
    assert new_bg.endswith("bg.png")

    restored = imp_store.get(out["id"])
    assert restored.galgame_tts == {"你好呀": "/uploads/bob/tts_1.mp3"}
    msg = restored.threads[0].messages[0].content
    assert msg.startswith("这里是 /uploads/bob/")
    assert "/uploads/admin/" not in msg

    from pathlib import Path
    assert (imp_up / Path(new_bg[len("/uploads/bob/"):])).read_bytes() == b"BGPNG"
    assert (imp_up / "tts_1.mp3").read_bytes() == b"MP3"


def test_import_zip_rejects_garbage(tmp_path):
    imp_store = JsonSessionStore(tmp_path / "store-x")
    sc = SimpleNamespace(uid="bob", level=1, store=imp_store,
                         lore=_FakeLore([]), upload_dir=str(tmp_path / "up-x"))
    with pytest.raises(HTTPException) as ei:
        _run(import_session_zip(_request(sc), _FakeUpload(b"garbage")))
    assert ei.value.status_code == 400


def test_import_zip_rejects_v2_envelope(tmp_path):
    # v2 JSON 信封塞进 zip：version 校验拒绝（zip 仅接受 v3）
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"files": []}))
        zf.writestr("conversation.json", json.dumps(
            {"type": "flyai-conversation", "version": 2, "data": {}}))

    imp_store = JsonSessionStore(tmp_path / "store-y")
    up = tmp_path / "up-y"
    up.mkdir()
    sc = SimpleNamespace(uid="bob", level=1, store=imp_store,
                         lore=_FakeLore([]), upload_dir=str(up))
    with pytest.raises(HTTPException) as ei:
        _run(import_session_zip(_request(sc), _FakeUpload(buf.getvalue())))
    assert ei.value.status_code == 400


def test_import_preserves_existing_member_override(tmp_path):
    envelope, store, request = _env(tmp_path, chars_in_library=True)
    # 导出口径：把第一个 AI 成员改成自带副本（override）
    m0 = envelope["data"]["members"][1]
    card = next(c for c in envelope["data"]["characters"] if c["id"] == m0["character_id"])
    m0["override"] = {k: v for k, v in card.items() if k != "ref"}
    m0["character_id"] = ""

    out = _call(envelope, request)
    s2 = store.get(out["id"])
    target = s2.member(m0["id"])
    assert target.override and not target.character_id
    # 另一成员仍按库引用计入复用
    assert out["import_summary"]["reused_cards"] == 1
