# -*- coding: utf-8 -*-
"""[2026-10-03] 「重置保留持仓历史」护栏（用户指令「继续」）。

## 为什么
决策↔成交回填的真实瓶颈不是匹配算法，而是 **`完整重置` 会 DELETE 持仓**：
365 天里 229 条已执行决策只有 8 条能回填（3.5%），抽样 40 条里 **22 条的最近同名持仓在 7 天以外**
—— 盈亏实体被重置删掉了，学习归因永久断链。

## 改法
`PAPER_RESET_KEEP_HISTORY`（**默认 true**）：
· **状态行必删**：`status='open'` 持仓 + `status='pending'` 订单（重置语义 = 没有仓位）；
· **历史行保留**：已平仓持仓 / 已成交订单（学习归因原料；余额只查 open 仓位，不受影响）。
实测：重置前 (open/closed/pending/filled)=(1,1,1,1) → 重置后 **(0,1,0,1)**。
回滚：`PAPER_RESET_KEEP_HISTORY=false` → 回到旧的"全删"行为（该分支代码仍保留）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_ENGINE = (ROOT / "backend/services/paper_trading_engine.py").read_text(encoding="utf-8")
SRC_PAGE = (ROOT / "frontend-next/src/app/paper-trading/page.tsx").read_text(encoding="utf-8")


def test_keep_history_default_true_and_only_state_rows_deleted():
    seg = SRC_ENGINE.split("def _purge_account_trade_rows(")[1].split("def set_initial_balance(")[0]
    assert 'PAPER_RESET_KEEP_HISTORY", "true"' in seg, "默认必须保留历史（否则回填率永远上不去）"
    keep = seg.split("if keep_history:")[1].split("pos_ids = [")[0]
    assert 'PaperPosition.status == "open"' in keep, "只删 open 持仓"
    assert 'PaperOrder.status == "pending"' in keep, "只删 pending 订单"
    assert "PaperPosition.account_id == account_id).delete(" not in keep, "保留分支不得全删持仓"
    assert '"history_kept": True' in keep


def test_full_delete_branch_kept_for_rollback():
    seg = SRC_ENGINE.split("def _purge_account_trade_rows(")[1].split("def set_initial_balance(")[0]
    assert "PositionExitEvent.position_id.in_(pos_ids)" in seg, "回滚分支（全删）必须保留，且仍先删子表"
    assert '"history_kept": False' in seg


def test_balance_calc_unaffected_by_kept_history():
    """保留历史不能影响余额：`_recalc_balance` 只应统计 open 仓位。"""
    seg = SRC_ENGINE.split("def _recalc_balance(")[1].split("\n    def ")[0]
    assert 'status == "open"' in seg or "status='open'" in seg


def test_ui_wording_mentions_history_kept():
    assert "已平仓历史保留" in SRC_PAGE, "UI 需说明重置会保留历史（避免误解为数据丢失）"
