# -*- coding: utf-8 -*-
"""策略记忆期望值闸门（M1-4 共享判据）。

StrategyMemory.sharpe_ratio 曾被三个不同公式共写（符号 EMA ∈[-1,1]、
mean/std*sqrt(252) 小样本爆值、coordinator 第三处），任何"sharpe>=0.5"的
晋升/冠军判定都量纲错误。统一改用期望值判据：

    EV = win_rate × avg_profit + (1 - win_rate) × avg_loss

avg_profit/avg_loss 由 learning 闭环增量维护（同为 USDT 口径），EV>0 且
avg_loss<0 才认为策略具有（统计上）正期望。样本门槛由调用方 total_trades 控制。
"""
from __future__ import annotations


def memory_expected_value(mem) -> float:
    """从策略记忆计算单笔期望值（USDT 口径）。"""
    try:
        wr = float(getattr(mem, "win_rate", 0) or 0)
        ap = float(getattr(mem, "avg_profit", 0) or 0)
        al = float(getattr(mem, "avg_loss", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return wr * ap + (1 - wr) * al


def memory_ev_ok(mem, *, min_trades: int = 15) -> bool:
    """正期望判定：EV>0 且平均亏损为负（亏损均值结构存在）。"""
    if mem is None:
        return False
    if int(getattr(mem, "total_trades", 0) or 0) < min_trades:
        return False
    ev = memory_expected_value(mem)
    avg_loss = float(getattr(mem, "avg_loss", 0) or 0)
    return ev > 0 and avg_loss < 0
