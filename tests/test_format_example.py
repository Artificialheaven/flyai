"""格式示范注入（few-shot）单测：位置、不破坏前缀、不写入历史。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.loop import _inject_format_example
from flyai.schemas import Message


def _hist():
    # 模拟：system 未被 loop 加入，history = 若干回合 + 末尾用户消息
    return [
        Message(role="user", content="早"),
        Message(role="assistant", content="（笑。）「早。」"),
        Message(role="user", content="在吗？"),
    ]


def test_inject_after_prefix_before_last_user():
    msgs = _hist()
    _inject_format_example(msgs, "（示例）「示例」")
    # 末尾仍是用户消息（不动最后一条本身）
    assert msgs[-1].role == "user" and msgs[-1].content == "在吗？"
    # 在它之前插入的是：提示 user + 示范 assistant
    assert msgs[-3].role == "user"
    assert msgs[-2].role == "assistant" and msgs[-2].content == "（示例）「示例」"


def test_prefix_unchanged():
    """注入示范后，历史前缀（除最末用户消息外）保持逐字节不变 → 前缀缓存不失效。"""
    hist = _hist()
    msgs = list(hist)
    _inject_format_example(msgs, "X")
    pre = len(hist) - 1  # 最末用户消息之前即稳定缓存前缀
    for i in range(pre):
        assert msgs[i].role == hist[i].role and msgs[i].content == hist[i].content
    # 注入后尾部为：示范提示 user + 示范 assistant +（原最后一条用户）
    assert msgs[pre].role == "user" and msgs[pre].content.startswith("（请严格遵循")
    assert msgs[pre + 1].role == "assistant" and msgs[pre + 1].content == "X"
    assert msgs[pre + 2].content == hist[-1].content
    # 仅多出尾部两段，长度 = 历史 + 2
    assert len(msgs) == len(hist) + 2


def test_no_inject_without_format():
    msgs = _hist()
    _inject_format_example(msgs, "")
    assert len(msgs) == len(_hist())


def test_no_inject_when_tail_not_user():
    msgs = _hist()
    msgs.append(Message(role="tool", content="结果", tool_call_id="t1"))  # 工具迭代中，尾部非 user
    _inject_format_example(msgs, "X")
    assert len(msgs) == 4  # 不注入