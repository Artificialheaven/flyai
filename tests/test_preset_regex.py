# -*- coding: utf-8 -*-
"""预设绑定的酒馆正则：导入解析 / 清洗 / 持久化 / 展示下发的开关语义。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api.routes_preset import (
    PresetPayload,
    create_preset,
    import_preset,
    update_preset,
)
from flyai.api.routes_session import _st_payload
from flyai.preset.models import Preset
from flyai.preset.store import PresetStore
from flyai.preset.tavern import preset_from_text, preset_to_envelope
from flyai.st.regex_scripts import MAX_SCRIPTS, sanitize_regex_scripts
from flyai.user import UserSettings

_SCRIPTS = [
    {"scriptName": "甲", "findRegex": "/甲/g", "replaceString": "A"},
    {"scriptName": "乙", "findRegex": "/乙/g", "replaceString": "B"},
]


def _prompt(identifier, content, **extra):
    raw = {"identifier": identifier, "name": identifier,
           "enabled": True, "injection_position": 1, "role": 0,
           "content": content}
    raw.update(extra)
    return raw


def _preset_doc(bindings_content):
    return {
        "name": "带正则",
        "temperature": 0.7,
        "prompts": [
            _prompt("main", "主提示词"),
            _prompt("regexes-bindings", bindings_content),
        ],
    }


def _run(coro):
    return asyncio.run(coro)


class _Upload:
    def __init__(self, data, filename):
        self._data = data if isinstance(data, bytes) else data.encode("utf-8")
        self.filename = filename

    async def read(self):
        return self._data


def _env(tmp_path):
    store = PresetStore(tmp_path / "presets")
    settings = SimpleNamespace(active_preset_id="")
    sc = SimpleNamespace(uid="u1", presets=store, user_settings=settings,
                         save_user_settings=lambda: None)
    state = SimpleNamespace(user=lambda request: sc)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return SimpleNamespace(sc=sc, request=request, store=store)


# ---- 导入解析 ----

def test_import_bindings_list_form():
    p, stats = preset_from_text(json.dumps(_preset_doc(json.dumps(_SCRIPTS))))
    assert len(p.regex_scripts) == 2
    assert stats["regex_scripts"] == 2
    assert stats["regex_invalid"] == 0
    # 绑定块不是 prompt，不得注入上下文
    assert len(p.blocks) == 1
    assert p.blocks[0].content == "主提示词"


def test_import_bindings_dict_wrapper():
    content = json.dumps({"scripts": _SCRIPTS[:1]})
    p, stats = preset_from_text(json.dumps(_preset_doc(content)))
    assert [s["findRegex"] for s in p.regex_scripts] == ["/甲/g"]
    assert stats["regex_scripts"] == 1


def test_import_bindings_id_map_and_double_encoding():
    mapped = json.dumps({
        "a": {"findRegex": "/甲/g", "replaceString": "A"},
        "b": {"findRegex": "/乙/g", "replaceString": "B"},
    })
    p, _ = preset_from_text(json.dumps(_preset_doc(mapped)))
    assert len(p.regex_scripts) == 2

    encoded = json.dumps(json.dumps(_SCRIPTS))
    p2, _ = preset_from_text(json.dumps(_preset_doc(encoded)))
    assert len(p2.regex_scripts) == 2


def test_import_bad_bindings_does_not_block():
    p, stats = preset_from_text(json.dumps(_preset_doc("{{{ 不是 json")))
    assert p.regex_scripts == []
    assert stats["regex_invalid"] == 1
    assert len(p.blocks) == 1


def test_import_identifier_normalization():
    doc = _preset_doc(json.dumps(_SCRIPTS))
    doc["prompts"][1]["identifier"] = "Regexes_Bindings"
    p, _ = preset_from_text(json.dumps(doc))
    assert len(p.regex_scripts) == 2


def test_import_top_level_regex_scripts_and_nested_prompts():
    doc = {"name": "n", "regex_scripts": _SCRIPTS,
           "chatCompletionSettings": {"prompts": [_prompt("main", "主")]}}
    p, stats = preset_from_text(json.dumps(doc))
    assert len(p.regex_scripts) == 2
    assert stats["regex_scripts"] == 2
    assert len(p.blocks) == 1


def test_envelope_roundtrip_keeps_regex():
    p = Preset(name="往返", regex_scripts=_SCRIPTS)
    text = json.dumps(preset_to_envelope(p))
    p2, stats = preset_from_text(text)
    assert p2.regex_scripts == _SCRIPTS
    assert stats["regex_scripts"] == 2


# ---- 清洗 ----

def test_sanitize_strips_unknown_and_missing_find():
    raw = [
        {"scriptName": "ok", "findRegex": "/x/g", "replaceString": "y",
         "evil": "<script>", "disabled": False},
        {"scriptName": "no-find"},
        "not-a-dict",
    ]
    out = sanitize_regex_scripts(raw)
    assert len(out) == 1
    assert out[0] == {"scriptName": "ok", "findRegex": "/x/g",
                      "replaceString": "y", "disabled": False}


def test_sanitize_count_limit():
    raw = [{"findRegex": "/a/g", "replaceString": "b"}] * (MAX_SCRIPTS + 5)
    assert len(sanitize_regex_scripts(raw)) == MAX_SCRIPTS


def test_from_dict_sanitizes_persisted_scripts():
    p = Preset.from_dict({"name": "x", "regex_scripts": [
        {"findRegex": "/a/g", "junk": 1},
    ]})
    assert p.regex_scripts == [{"findRegex": "/a/g"}]


# ---- API：导入门槛 / PUT 保留语义 ----

def test_api_import_regex_only_preset(tmp_path):
    env = _env(tmp_path)
    doc = {"name": "只有正则", "prompts": [
        _prompt("regexes-bindings", json.dumps(_SCRIPTS)),
    ]}
    out = _run(import_preset(
        env.request, file=_Upload(json.dumps(doc), "regex.json"),
    ))
    assert out["stats"]["regex_scripts"] == 2
    assert out["preset"]["blocks"] == []
    assert len(env.store.get(out["preset"]["id"]).regex_scripts) == 2


def test_api_import_regex_only_with_empty_prompts_rejected(tmp_path):
    env = _env(tmp_path)
    with pytest.raises(HTTPException) as e:
        _run(import_preset(
            env.request, file=_Upload(json.dumps({"prompts": []}), "x.json"),
        ))
    assert e.value.status_code == 400


def test_api_put_preserves_regex_when_omitted(tmp_path):
    env = _env(tmp_path)
    created = create_preset(PresetPayload(name="p", regex_scripts=_SCRIPTS),
                            env.request)
    pid = created["id"]
    assert len(created["regex_scripts"]) == 2

    updated = update_preset(pid, PresetPayload(name="改名"), env.request)
    assert len(updated["regex_scripts"]) == 2

    cleared = update_preset(pid, PresetPayload(name="清空", regex_scripts=[]),
                            env.request)
    assert cleared["regex_scripts"] == []


# ---- 展示侧下发 ----

def _session():
    return SimpleNamespace(members=[], character_id="", mvu={})


def test_st_payload_includes_global_scripts():
    payload = _st_payload(_session(), None, None, UserSettings(),
                          preset_scripts=_SCRIPTS)
    assert payload["regex_scripts_global"] == _SCRIPTS


def test_st_payload_global_empty_when_display_off():
    settings = UserSettings(st_regex_display_enabled=False)
    payload = _st_payload(_session(), None, None, settings,
                          preset_scripts=_SCRIPTS)
    assert payload["regex_scripts_global"] == []


def test_st_payload_global_empty_by_default():
    payload = _st_payload(_session(), None, None, UserSettings())
    assert payload["regex_scripts_global"] == []
