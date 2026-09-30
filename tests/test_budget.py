"""字符预算截断：fit_budget 段落/句界/硬切，fit_blocks 整块丢弃。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.budget import ELLIPSIS, fit_blocks, fit_budget


def test_limit_zero_or_none_returns_original():
    text = "任意长度的内容。\n第二段。"
    assert fit_budget(text, 0) == text
    assert fit_budget(text, None) == text


def test_within_limit_unchanged():
    text = "短内容"
    assert fit_budget(text, 100) == text


def test_paragraph_level_drops_whole_paragraph():
    text = "第一段内容。\n第二段也要保留吗。\n第三段超长。"
    out = fit_budget(text, 12)
    assert "第一段内容。" in out
    assert "第二段" not in out
    assert out.endswith(ELLIPSIS)
    assert len(out) <= 12


def test_sentence_boundary_within_paragraph():
    text = "第一句很短。第二句也短。第三句会很长导致超出预算。"
    out = fit_budget(text, 11)
    assert "第一句很短。" in out
    assert "第二句" not in out
    assert out.endswith(ELLIPSIS)
    assert len(out) <= 11


def test_hard_cut_first_sentence_too_long():
    text = "这是一句没有任何标点分隔的超长句子内容啊啊啊啊啊啊啊"
    out = fit_budget(text, 8)
    assert out.endswith(ELLIPSIS)
    assert len(out) <= 8


def test_crlf_normalized():
    text = "第一段\r\n第二段"
    out = fit_budget(text, 6)
    assert "\r" not in out
    assert len(out) <= 6


def test_tiny_limit_below_ellipsis():
    out = fit_budget("随便什么内容", 1)
    assert out == "…"


def test_fit_blocks_keeps_whole_blocks_and_marks_drop():
    blocks = ["第一轮内容", "第二轮内容", "第三轮内容"]
    kept, dropped = fit_blocks(blocks, 11)
    # 整块不截半：放不下后续轮次时只在末块加省略标记
    assert kept == ["第一轮内容" + ELLIPSIS]
    assert dropped is True


def test_fit_blocks_no_limit_keeps_all():
    blocks = ["甲", "乙"]
    kept, dropped = fit_blocks(blocks, 0)
    assert kept == ["甲", "乙"]
    assert dropped is False


def test_fit_blocks_first_block_truncated_not_half():
    kept, dropped = fit_blocks(["第一块内容超级长", "第二块"], 6)
    assert len(kept) == 1
    assert len(kept[0]) <= 6
    assert kept[0].endswith(ELLIPSIS)
    assert dropped is True


def test_fit_blocks_empty():
    assert fit_blocks([], 10) == ([], False)
