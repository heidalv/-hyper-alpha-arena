# -*- coding: utf-8 -*-
"""E2 现金流引擎（v3 方向 5，p1-cashflow，2026-09-03）。

  e2a_aster_te     Aster Trade & Earn 真实收入账本定时刷新（wrap live_income_ledger）
  e2b_carry_sim    多场所资金费 carry 模拟（perp_funding → 矩阵 → Paper 双腿）
  idle_earn        Binance Simple Earn 活期申购/赎回（默认观察/半自动）
  jobs             APScheduler 注册

Phase 1 验收：T&E 账本可定时落盘；carry 模拟在 ≥2 场所有 perp_funding 时能跑通 Paper 双腿；
闲置理财任务可列出产品并在 CASHFLOW_IDLE_EARN_LIVE=true 时申购；返佣配置快照可读。
"""
from backend.services.cashflow.e2a_aster_te import refresh_all_aster_ledgers, aster_te_status
from backend.services.cashflow.e2b_carry_sim import run_carry_sim, carry_sim_latest
from backend.services.cashflow.idle_earn import idle_earn_tick, idle_earn_status
from backend.services.cashflow.rebate_snapshot import snapshot_rebate_config

__all__ = [
    "refresh_all_aster_ledgers", "aster_te_status",
    "run_carry_sim", "carry_sim_latest",
    "idle_earn_tick", "idle_earn_status",
    "snapshot_rebate_config",
]
