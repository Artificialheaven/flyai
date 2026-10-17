"""剧情台账每轮自动注入：render_ledger_reminder + build_ledger_block 门控。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_chat import build_ledger_block
from flyai.config import AppConfig
from flyai.lore.character_memory import CharacterMemoryStore
from flyai.memory.ledger import LedgerItem, render_ledger_reminder
from flyai.sessions.models import RoomMember
from flyai.user import UserScope


def _ledger():
    return {
        "character": [],
        "item": [LedgerItem(kind="item", name="铁剑", state="随身佩戴",
                            status="active", updated_at="2026-10-11")],
        "location": [],
        "task": [
            LedgerItem(kind="task", name="旧委托", state="已交付",
                       status="done", updated_at="2026-10-12"),
            LedgerItem(kind="task", name="寻找商队", state="追到黑森林",
                       status="open", updated_at="2026-10-16"),
        ],
        "foreshadow": [
            LedgerItem(kind="foreshadow", name="神秘信物", state="母亲遗物",
                       status="planted", updated_at="2026-10-09"),
        ],
    }


# ---- 纯渲染 ----

def test_render_only_active_items():
    block = render_ledger_reminder(_ledger())
    assert "【剧情状态】" in block
    assert "寻找商队" in block and "神秘信物" in block and "铁剑" in block
    # 已终结的不入概览
    assert "旧委托" not in block
    # 物品只有 active 一种态，不带冗余状态词；任务/伏笔带中文状态
    assert "[任务·进行中] 寻找商队：追到黑森林" in block
    assert "[伏笔·已埋下] 神秘信物" in block
    assert "[物品] 铁剑" in block


def test_render_empty_ledger():
    assert render_ledger_reminder({}) == ""


def test_render_respects_budget():
    ledger = {
        "character": [], "location": [], "foreshadow": [],
        "item": [LedgerItem(kind="item", name="长物", state="x" * 500,
                            status="active")],
        "task": [LedgerItem(kind="task", name="短任务", state="待办",
                            status="open")],
    }
    block = render_ledger_reminder(ledger, budget=120)
    assert "短任务" in block  # 超长条目被跳过后，短条目仍能进
    assert "x" * 200 not in block


# ---- 注入门控 ----

def _scope(tmp_path, auto_recall):
    scope = UserScope.build("u1", AppConfig(), tmp_path, None)
    scope.user_settings.auto_recall = auto_recall
    return scope


def test_block_injected_for_own_card(tmp_path):
    scope = _scope(tmp_path, True)
    scope.char_memory.save_ledger("TA", _ledger())
    block = build_ledger_block(scope, "TA", None)  # 旧单 AI 路径 member=None
    assert "寻找商队" in block


def test_block_off_when_auto_recall_disabled(tmp_path):
    scope = _scope(tmp_path, False)
    scope.char_memory.save_ledger("TA", _ledger())
    assert build_ledger_block(scope, "TA", None) == ""


def test_block_hidden_for_other_card_without_mount(tmp_path):
    """座位挂别人卡、未显式挂载记忆簿：默认不可见，不注入。"""
    scope = _scope(tmp_path, True)
    scope.char_memory.save_ledger("OTHER", _ledger())
    seat = RoomMember(name="分身", character_id="SELF")
    assert build_ledger_block(scope, "SELF", seat) == ""


def test_block_visible_with_ro_mount(tmp_path):
    scope = _scope(tmp_path, True)
    scope.char_memory.save_ledger("OTHER", _ledger())
    seat = RoomMember(name="分身", character_id="SELF",
                      memory_mode="ro", memory_char_id="OTHER")
    block = build_ledger_block(scope, "SELF", seat)
    assert "寻找商队" in block


def test_block_no_store_returns_empty(tmp_path):
    scope = _scope(tmp_path, True)
    plain = SimpleNamespace(user_settings=SimpleNamespace(auto_recall=True))
    assert build_ledger_block(plain, "TA", None) == ""
