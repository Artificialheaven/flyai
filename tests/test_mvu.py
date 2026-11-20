"""P4 原生 MVU：InitVar、JSON Patch、更新块解析、会话状态快照/回退。"""

from __future__ import annotations

import pytest

from flyai.lore.models import Character
from flyai.sessions.models import Message, Session
from flyai.st import mvu
from flyai.tavern import render_tavern_macros

INIT_YAML = """\
```yaml
stat_data:
  姓名: 零号
  属性:
    体力: 10
    金钱: 5
  物品:
    - 符纸
```

```yaml
stat_data:
  属性:
    体力: 12
```

"""


def _card(**kwargs) -> Character:
    return Character(name="零号", **kwargs)


def _mvu_card() -> Character:
    return _card(table=[{"title": "[InitVar]", "content": INIT_YAML}])


# ---- InitVar ----


def test_load_init_var_deep_merge_and_fence():
    state, warnings = mvu.load_init_var(
        [{"title": "[InitVar]", "content": INIT_YAML}]
    )
    assert warnings == []
    assert state["姓名"] == "零号"
    assert state["属性"] == {"体力": 12, "金钱": 5}
    assert state["物品"] == ["符纸"]


def test_load_init_var_ignores_other_entries_and_bad_yaml():
    state, warnings = mvu.load_init_var(
        [
            {"title": "普通条目", "content": "a: 1"},
            {"title": "[InitVar]", "content": "a: [unclosed"},
        ]
    )
    assert state == {}
    assert any("YAML" in w for w in warnings)


def test_load_init_var_rejects_non_mapping():
    state, warnings = mvu.load_init_var(
        [{"title": "[InitVar]", "content": "- 1\n- 2\n"}]
    )
    assert state == {}
    assert warnings


def test_load_init_var_size_limit():
    big = "a: " + "x" * (mvu.MAX_INIT_YAML + 10)
    state, warnings = mvu.load_init_var([{"title": "[InitVar]", "content": big}])
    assert state == {}
    assert any("超过" in w for w in warnings)


def test_card_uses_mvu_by_initvar_or_script():
    assert mvu.card_uses_mvu(_mvu_card())
    assert mvu.card_uses_mvu(
        _card(regex_scripts=[{"replaceString": "<UpdateVariable>"}])
    )
    assert not mvu.card_uses_mvu(_card())


INLINE_FIRST_MES = """\
开场正文
<UpdateVariable>
<InitVar>
```yaml
stat_data:
  姓名: 内联
  属性:
    体力: 20
```
</InitVar>
</UpdateVariable>
"""


def test_card_uses_mvu_by_inline_init_and_mvu_update_title():
    # 开场白内联 <InitVar>：不需要 [InitVar] 条目也能识别
    assert mvu.card_uses_mvu(_card(first_mes=INLINE_FIRST_MES))
    # 标题为 [mvu_update]… 的世界书条目同样是声明信号
    assert mvu.card_uses_mvu(
        _card(table=[{"title": "[mvu_update]变量输出格式", "content": ""}])
    )
    # 正文提到 <UpdateVariable> 不算（大量教程类非 MVU 卡会误报）
    assert not mvu.card_uses_mvu(_card(first_mes="提到 <UpdateVariable> 的教程"))


def test_load_init_var_inline_first_mes():
    state, warnings = mvu.load_init_var([], INLINE_FIRST_MES)
    assert warnings == []
    assert state == {"姓名": "内联", "属性": {"体力": 20}}


def test_load_init_var_inline_overrides_table():
    table = [{"title": "[InitVar]", "content": "stat_data:\n  姓名: 表\n  属性:\n    体力: 10\n"}]
    state, warnings = mvu.load_init_var(table, INLINE_FIRST_MES)
    assert warnings == []
    # 内联更贴近本局实际开场：同名键由内联覆盖，表内独有键保留
    assert state["姓名"] == "内联"
    assert state["属性"] == {"体力": 20}


def test_load_init_var_inline_bad_yaml_only_warns():
    text = "<InitVar>\n```yaml\na: [unclosed\n```\n</InitVar>"
    state, warnings = mvu.load_init_var([], text)
    assert state == {}
    assert any("YAML" in w for w in warnings)


def test_load_init_var_inline_handles_indented_block():
    # 卡内常见：<InitVar> 后紧跟缩进的 YAML，整块去缩进后才能解析
    text = "<InitVar>\n  stat_data:\n    hp: 3\n</InitVar>"
    state, warnings = mvu.load_init_var([], text)
    assert warnings == []
    assert state == {"hp": 3}


def test_build_initial_state_from_inline_init():
    card = _card(first_mes=INLINE_FIRST_MES)
    state, warnings = mvu.build_initial_state(card)
    assert warnings == []
    assert state["enabled"] and state["initialized"]
    assert state["stat_data"]["姓名"] == "内联"
    # 无任何 MVU 痕迹的卡仍禁用
    assert mvu.build_initial_state(_card())[0] == {"enabled": False}


def test_build_initial_state():
    card = _mvu_card()
    state, warnings = mvu.build_initial_state(card)
    assert warnings == []
    assert state["enabled"] and state["initialized"]
    assert state["char_id"] == card.id
    assert state["stat_data"] == state["initial"]
    assert state["initial"]["姓名"] == "零号"
    # 深拷贝：修改当前状态不影响初值
    state["stat_data"]["姓名"] = "改名"
    assert state["initial"]["姓名"] == "零号"


# ---- JSON Patch ----


def test_patch_add_replace_remove():
    doc = {"a": 1, "b": {"c": 2}}
    out = mvu.apply_patch(
        doc,
        [
            {"op": "add", "path": "/b/d", "value": 3},
            {"op": "replace", "path": "/a", "value": 9},
            {"op": "remove", "path": "/b/c"},
        ],
    )
    assert out == {"a": 9, "b": {"d": 3}}
    assert doc == {"a": 1, "b": {"c": 2}}  # 原文档不被修改


def test_patch_array_append_and_insert():
    out = mvu.apply_patch(
        {"items": ["a"]},
        [
            {"op": "add", "path": "/items/-", "value": "b"},
            {"op": "add", "path": "/items/0", "value": "z"},
        ],
    )
    assert out["items"] == ["z", "a", "b"]


def test_patch_delta_and_insert_extensions():
    out = mvu.apply_patch(
        {"属性": {"体力": 10}, "物品": ["符纸", "朱砂"], "flag": 0},
        [
            {"op": "delta", "path": "/属性/体力", "value": -3},
            # 模型常把增量写成带符号字符串
            {"op": "delta", "path": "/flag", "value": "+2"},
            # 键不存在时从 0 起算
            {"op": "delta", "path": "/属性/精神", "value": "5"},
            {"op": "insert", "path": "/物品/1", "value": "铜钱"},
            {"op": "insert", "path": "/物品/-", "value": "罗盘"},
            {"op": "insert", "path": "/称号", "value": "夜班"},
        ],
    )
    assert out["属性"] == {"体力": 7, "精神": 5}
    assert out["物品"] == ["符纸", "铜钱", "朱砂", "罗盘"]
    assert out["flag"] == 2
    assert out["称号"] == "夜班"


def test_patch_delta_rejects_non_numeric():
    with pytest.raises(mvu.PatchError):
        mvu.apply_patch({"a": 1}, [{"op": "delta", "path": "/a", "value": "很多"}])
    # bool 不当作 1/0 静默累加
    with pytest.raises(mvu.PatchError):
        mvu.apply_patch({"a": 1}, [{"op": "delta", "path": "/a", "value": True}])


def test_patch_move_copy_test():
    out = mvu.apply_patch(
        {"a": {"x": 1}, "b": {}},
        [
            {"op": "copy", "from": "/a", "path": "/b/copy"},
            {"op": "move", "from": "/a/x", "path": "/b/x"},
            {"op": "test", "path": "/b/x", "value": 1},
        ],
    )
    assert out == {"a": {}, "b": {"copy": {"x": 1}, "x": 1}}


def test_patch_test_failure_raises():
    with pytest.raises(mvu.PatchError):
        mvu.apply_patch(
            {"a": 1},
            [{"op": "test", "path": "/a", "value": 2}],
        )


def test_patch_atomic_failure_keeps_original():
    # 第二个 op 失败：整个 patch 抛错，apply_updates 丢弃整块
    doc = {"a": 1}
    with pytest.raises(mvu.PatchError):
        mvu.apply_patch(
            doc,
            [
                {"op": "replace", "path": "/a", "value": 2},
                {"op": "remove", "path": "/missing"},
            ],
        )
    out, applied, warnings = mvu.apply_updates(
        doc,
        "<UpdateVariable><JSONPatch>"
        '[{"op":"replace","path":"/a","value":2},'
        '{"op":"remove","path":"/missing"}]'
        "</JSONPatch></UpdateVariable>",
    )
    assert out == {"a": 1}
    assert applied == 0
    assert warnings


def test_patch_path_normalization():
    for path in ("/stat_data/a", "/a", "stat_data.a"):
        out = mvu.apply_patch({"a": 1}, [{"op": "replace", "path": path, "value": 2}])
        assert out == {"a": 2}


def test_patch_json_pointer_escapes():
    out = mvu.apply_patch(
        {"a/b": 1, "m~n": 2},
        [
            {"op": "replace", "path": "/a~1b", "value": 3},
            {"op": "replace", "path": "/m~0n", "value": 4},
        ],
    )
    assert out == {"a/b": 3, "m~n": 4}


def test_patch_root_must_stay_object():
    with pytest.raises(mvu.PatchError):
        mvu.apply_patch({"a": 1}, [{"op": "replace", "path": "", "value": 5}])


def test_patch_invalid_ops():
    with pytest.raises(mvu.PatchError):
        mvu.apply_patch({}, "not-a-list")
    with pytest.raises(mvu.PatchError):
        mvu.apply_patch({}, [{"op": "bogus", "path": "/a"}])
    with pytest.raises(mvu.PatchError):
        mvu.apply_patch({}, [{"op": "add", "path": "a", "value": 1}])


# ---- <UpdateVariable> ----


def test_extract_patches_variants():
    text = (
        "正文\n"
        "<updatevariable><jsonpatch>"
        '[{"op":"replace","path":"/hp","value":9}]'
        "</jsonpatch></updatevariable>\n"
        "<UpdateVariable>"
        "```json\n"
        '[{"op":"add","path":"/mp","value":1}]\n'
        "```"
        "</UpdateVariable>"
    )
    patches, warnings = mvu.extract_patches(text)
    assert warnings == []
    assert len(patches) == 2
    assert patches[0][0]["path"] == "/hp"
    assert patches[1][0]["path"] == "/mp"


def test_extract_patches_invalid_json_warns():
    patches, warnings = mvu.extract_patches(
        "<UpdateVariable>{not json}</UpdateVariable>"
    )
    assert patches == []
    assert warnings


def test_extract_patches_non_array_warns():
    patches, warnings = mvu.extract_patches(
        "<UpdateVariable>123</UpdateVariable>"
    )
    assert patches == []
    assert warnings


def test_apply_updates_multiple_blocks_continue_after_failure():
    text = (
        "<UpdateVariable><JSONPatch>"
        '[{"op":"replace","path":"/hp","value":9}]'
        "</JSONPatch></UpdateVariable>"
        "<UpdateVariable><JSONPatch>"
        '[{"op":"remove","path":"/missing"}]'
        "</JSONPatch></UpdateVariable>"
        "<UpdateVariable><JSONPatch>"
        '[{"op":"add","path":"/log","value":"ok"}]'
        "</JSONPatch></UpdateVariable>"
    )
    state, applied, warnings = mvu.apply_updates({"hp": 1}, text)
    assert applied == 2
    assert state == {"hp": 9, "log": "ok"}
    assert len(warnings) == 1


def test_strip_update_blocks_keeps_rest():
    text = "前<UpdateVariable><JSONPatch>[]</JSONPatch></UpdateVariable>后"
    assert mvu.strip_update_blocks(text) == "前后"
    assert mvu.strip_update_blocks(None) is None
    # 独立（不在 <UpdateVariable> 内）的初值块同样剥离
    assert mvu.strip_update_blocks("前<InitVar>a: 1</InitVar>后") == "前后"


# ---- 会话状态 ----


class _Msg:
    def __init__(self, role="assistant", content="", stat_data=None, hidden=False):
        self.role = role
        self.content = content
        self.stat_data = stat_data
        self.hidden = hidden


def test_last_state_skips_hidden_and_non_assistant():
    msgs = [
        _Msg("assistant", "", {"a": 1}),
        _Msg("user", "", {"a": 99}),
        _Msg("assistant", "", {"a": 2}, hidden=True),
    ]
    assert mvu.last_state(msgs) == {"a": 1}
    assert mvu.last_state([_Msg("user", "", {"a": 1})]) is None


def test_ensure_session_state_lifecycle():
    s = Session()
    card = _mvu_card()
    assert mvu.ensure_session_state(s, card) is True
    assert s.mvu["enabled"] and s.mvu["stat_data"]["姓名"] == "零号"
    assert mvu.ensure_session_state(s, card) is False  # 幂等

    other = _mvu_card()
    other.table = [{"title": "[InitVar]", "content": "hp: 3\n"}]
    assert mvu.ensure_session_state(s, other) is True
    assert s.mvu["stat_data"] == {"hp": 3}

    assert mvu.ensure_session_state(s, _card()) is True
    assert s.mvu == {}


def test_ensure_session_state_keeps_state_when_carrier_missing():
    """卡被删除/重导入换 id 时不能清空状态（否则打开会话就抹掉历史数值）。"""
    s = Session()
    mvu.ensure_session_state(s, _mvu_card())
    s.mvu["stat_data"] = {"hp": 7}
    assert mvu.ensure_session_state(s, None) is False
    assert s.mvu["stat_data"] == {"hp": 7}


class _Lore:
    def get_character(self, cid):
        return None


def test_sync_session_state_recovers_orphan_from_snapshots():
    s = Session(character_id="deleted-card")
    s.messages = [
        Message(role="assistant", content="", stat_data={"hp": 5}),
        Message(role="assistant", content="", stat_data={"hp": 4}),
    ]
    assert mvu.sync_session_state(s, _Lore()) is True
    assert s.mvu["enabled"] is True
    assert s.mvu["stat_data"] == {"hp": 4}
    # 幂等：再次同步不再改动
    assert mvu.sync_session_state(s, _Lore()) is False
    assert s.mvu["stat_data"] == {"hp": 4}


def test_sync_session_state_orphan_recomputes_from_snapshots():
    s = Session(character_id="deleted-card")
    mvu.ensure_session_state(s, _mvu_card())
    s.mvu["stat_data"] = {"hp": 9}
    s.messages = [Message(role="assistant", content="", stat_data={"hp": 3})]
    assert mvu.sync_session_state(s, _Lore()) is True
    # 按最后快照重算，而不是被清空或沿用旧值
    assert s.mvu["stat_data"] == {"hp": 3}


def test_recompute_state_rewind():
    s = Session()
    mvu.ensure_session_state(s, _mvu_card())
    s.mvu["stat_data"] = {"hp": 5}
    msgs = [
        _Msg("assistant", "", {"hp": 5}),
        _Msg("assistant", "", {"hp": 4}),
    ]
    assert mvu.recompute_state(s, msgs) is True
    assert s.mvu["stat_data"] == {"hp": 4}
    assert mvu.recompute_state(s, msgs) is False

    # 全部快照被撤掉：回到 InitVar 初值
    assert mvu.recompute_state(s, [_Msg("assistant", "")]) is True
    assert s.mvu["stat_data"] == s.mvu["initial"]


def test_recompute_state_noop_without_mvu():
    s = Session()
    assert mvu.recompute_state(s, [_Msg("assistant", "", {"a": 1})]) is False


def test_mvu_active_gates():
    card = _mvu_card()

    class _Settings:
        def __init__(self, value):
            self.mvu_enabled = value

    class _Cfg:
        class mvu:
            enabled = True

    assert mvu.mvu_active(card, _Settings(True), _Cfg) is True
    assert mvu.mvu_active(card, _Settings(False), _Cfg) is False
    assert mvu.mvu_active(card, _Settings(None), _Cfg) is True
    assert mvu.mvu_active(_card(), _Settings(True), _Cfg) is False

    class _OffCfg:
        class mvu:
            enabled = False

    assert mvu.mvu_active(card, _Settings(True), _OffCfg) is False


def test_snapshot_messages_prefers_primary_thread():
    s = Session()
    t = s.thread("missing")
    assert t is None
    from flyai.sessions.models import Thread

    dm = Thread(kind="dm", messages=[Message(role="assistant", content="x")])
    s.threads = [dm]
    s.messages = [Message(role="user", content="legacy")]
    assert mvu.snapshot_messages(s) == dm.messages


# ---- 宏展开 ----


def test_render_tavern_macros_variables():
    variables = {"stat_data": {"属性": {"体力": 12}, "物品": ["符纸"]}}
    text = "体力={{format_message_variable::stat_data.属性.体力}}"
    assert render_tavern_macros(text, variables=variables) == "体力=12"
    assert (
        render_tavern_macros("{{format_message_variable::stat_data.物品[0]}}", variables=variables)
        == "符纸"
    )
    assert (
        render_tavern_macros("{{format_message_variable::stat_data.缺失}}", variables=variables)
        == ""
    )
    # 未提供 variables 时变量宏按未知宏剥除，且不影响其它文本
    assert render_tavern_macros(text) == "体力="
