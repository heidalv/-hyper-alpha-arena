# -*- coding: utf-8 -*-
"""[整顿轮·T2 2026-10-05] 吃单进场必须是「成本门槛」，不是拍脑袋的常数。

锁定的契约：
  1. 吃单进场的门槛 = 「吃单进 + 挂单出」的往返成本（由唯一成本真相源给出）
  2. 该门槛**远大于**旧的 0.9bp（旧门槛允许 0.9bp 的预期去付 9bp 的成本）
  3. `MM_TAKER_ENTRY_COST_MULT=0` 时门槛归零 ⇒ 逐字回滚旧行为
  4. 门槛不通过时**退回挂单进场**，而不是拒绝开仓（机会不减少）

背景实测（lane_ledger，近 24h）：
  · `flow_entry_maker` 554 腿  均 spread **+6.15bp**  净 **+$63.17**
  · `flow_entry_taker` 220 腿  均 spread **−4.24bp**  净 **−$16.31**
  两者唯一差别是"过价 vs 挂单"；模型捕获只有约 +0.56bp。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from backend.services.fee_schedule_service import break_even_move_bp

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "backend/services/market_maker/runner.py"

# 旧门槛（已被替换）。保留为常量，用于断言"新门槛必须严格更严"。
OLD_TAKER_ENTRY_THRESHOLD_BP = 0.9


def _runner_src() -> str:
    return RUNNER.read_text(encoding="utf-8", errors="replace")


class TestCostLayerValues:
    """成本真相源本身。"""

    def test_taker_in_maker_out_costs_nine_bp(self):
        need = break_even_move_bp(
            exchange="asterdex", entry_is_maker=False, exit_is_maker=True,
            hold_seconds=90)
        assert 8.0 < need < 10.0, f"吃单进+挂单出应约 9bp，实际 {need}"

    def test_maker_in_maker_out_is_free(self):
        # 双向挂单：asterdex maker 费 0 + 挂单不承担滑点 ⇒ 成本只剩
        # 持仓期的**资金费分摊**（90s / 8h × 1bp ≈ 0.003bp，可忽略但不为零）。
        need = break_even_move_bp(
            exchange="asterdex", entry_is_maker=True, exit_is_maker=True,
            hold_seconds=90)
        assert need < 0.01, (
            f"asterdex 双向挂单的盈亏平衡应≈0（仅资金费分摊），实际 {need}bp")
        # 与"吃单进"的 9bp 相比，必须低两个数量级
        taker_in = break_even_move_bp(
            exchange="asterdex", entry_is_maker=False, exit_is_maker=True,
            hold_seconds=90)
        assert taker_in > 100 * need, "吃单进场的成本必须远高于挂单进场"

    def test_new_threshold_is_much_stricter_than_old(self):
        need = break_even_move_bp(
            exchange="asterdex", entry_is_maker=False, exit_is_maker=True,
            hold_seconds=90)
        assert need > 5 * OLD_TAKER_ENTRY_THRESHOLD_BP, (
            f"新门槛({need}bp)必须远严于旧门槛({OLD_TAKER_ENTRY_THRESHOLD_BP}bp)"
            " —— 旧门槛允许预期(0.9bp)远小于成本(9bp)")


class TestRunnerWiring:
    """`runner.py` 必须真的用成本层，且不得残留旧的 0.9 常数。"""

    def test_runner_uses_cost_layer(self):
        src = _runner_src()
        assert "break_even_move_bp" in src, (
            "runner 必须从唯一成本真相源取门槛，不得内联数字")

    def test_old_magic_threshold_removed(self):
        src = _runner_src()
        # 旧写法：abs(float(_gate.get("mean_y") or 0.0)) >= 0.9
        bad = re.search(
            r'abs\(\s*float\(\s*_gate\.get\(\s*["\']mean_y["\']\s*\)\s*or\s*0\.0\s*\)'
            r'\s*\)\s*>=\s*0\.9', src)
        assert bad is None, (
            "旧的 `|mean_y| >= 0.9` 门槛必须已被删除"
            f"（在此处仍存在: {bad.group(0) if bad else ''}）")

    def test_threshold_computed_before_use(self):
        src = _runner_src()
        i_def = src.find("_need_taker_bp = 0.0")
        i_use = src.find("_need_taker_bp > 0")
        assert i_def != -1 and i_use != -1
        assert i_def < i_use, "门槛必须先计算、后使用"

    def test_rollback_switch_documented(self):
        src = _runner_src()
        assert "MM_TAKER_ENTRY_COST_MULT" in src, (
            "必须提供回滚开关，否则无法 A/B 对照与快速回退")

    def test_fallback_is_maker_entry_not_rejection(self):
        """门槛不过 ⇒ 退回挂单进场（active_flow 默认路径），不是拒绝开仓。"""
        src = _runner_src()
        # taker_entry 为 False 时不应出现"直接 return/拒绝"的分支
        assert "if not taker_entry" not in src, (
            "门槛不过不得直接拒绝开仓 —— 应回退到挂单进场，机会不减少")
