# -*- coding: utf-8 -*-
r"""[整顿轮·T4 2026-10-06] 出场仲裁阶梯必须与文档 `03_出场仲裁.md` **逐行一致**。

为什么需要这个测试：本文件的阶梯是第 17 轮新写的文档，
而它第一版就写错了（漏了 `taker_risk`、把 `no_book` 当成第一序）。
**文档与代码漂移**正是"整个系统没有统一贯彻风格"的根源之一，
所以要把"顺序"钉成可执行的契约，而不是靠人记得。

做法：从 `choose_exit` 源码里提取 `return "xxx"` 的顺序，
与文档表格里的顺序逐项比对。任一漂移即失败。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / "backend/services/market_maker/flow_rules.py"
DOC = ROOT.parent / "HFT_整顿" / "03_出场仲裁.md"

# 期望的判据顺序（与文档第二节表格一致）
EXPECTED = [
    "flat",          # 195-196  qty == 0
    "no_book",       # 197-198  盘口缺失
    "taker_risk",    # 199-200  R4/R5 或盘口过期 ⇒ 吃单
    "taker_stop",    # 201-204  浮亏 ≥ 2×止损
    "maker_take",    # 207-209  浮盈达标
    "maker_time",    # 210-211  opened_ts <= 0
    "maker_risk",    # 242-248  浮亏 ≥ max(5bp, 止损×比例)
    "maker_edge",    # 251-252  持仓够久且剩余期望 ≤ 0
    "maker_time",    # 254-256  持仓够久
    "hold",          # 257      兜底
]


def _choose_exit_returns() -> list:
    src = FLOW.read_text(encoding="utf-8", errors="replace")
    start = src.find("def choose_exit(")
    assert start > 0, "找不到 choose_exit"
    body = src[start:]
    # 到下一个顶层 def 为止
    nxt = body.find("\ndef ", 10)
    if nxt > 0:
        body = body[:nxt]
    return re.findall(r'return "(\w+)"', body)


def test_choose_exit_order_matches_doc():
    got = _choose_exit_returns()
    assert got == EXPECTED, (
        "choose_exit 的 return 顺序与文档不符\n"
        f"  代码: {got}\n  文档: {EXPECTED}\n"
        "若是有意改动，必须同时更新 03_出场仲裁.md 并给出前后数字")


def test_doc_contains_all_paths():
    doc = DOC.read_text(encoding="utf-8", errors="replace")
    for name in set(EXPECTED):
        assert f"`{name}`" in doc, f"文档缺少出口 `{name}`"


def test_doc_records_the_correction():
    """第一版写错过，必须留下更正记录（防止后人以为文档一直是对的）。"""
    doc = DOC.read_text(encoding="utf-8", errors="replace")
    assert "我要更正自己" in doc, "文档必须保留自我更正的记录"


def test_doc_states_maker_first_rule():
    doc = DOC.read_text(encoding="utf-8", errors="replace")
    assert "能用挂单的场合一律不用吃单" in doc, "唯一铁律必须写在文档里"
