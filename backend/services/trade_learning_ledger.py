# -*- coding: utf-8 -*-
"""把**生产交易事件**接入统一进化学习账本（[2026-10-03 补齐「①决策 → 血缘账本」断链]）。

## 为什么（实测证据）
`/api/learning/events` 长期只有 **4 条**事件，来源全是 `selftest` / `signal_backtest` /
`backtest_loop` —— 真实开仓/平仓**从未入账**。于是链路体检里「①决策 → 血缘账本」标为**停滞**：
学习链路看不到生产上到底发生了什么，所谓"从交易中学习"缺了最上游的一环。

## 设计（与账本既有语义对齐）
账本 `EvolutionEnvelope` 的合法 stage = `deploy/evolve/feedback/hypothesis/learn/observe/rl_decide/validate`，
status = `pending/passed/rejected/deployed/rolled_back`。本模块：
  · **开仓** → stage=`observe`（观察一次真实决策落地），status=`pending`；
  · **平仓** → stage=`feedback`（把结果反馈进链路），status = `passed`(盈利) / `rejected`(亏损)；
  · 同一笔交易用**确定性 lineage_id**：`lin_trade_{account_id}_{position_id}` ⇒ open 与 close
    自动落在同一条血缘链路上，无需新增状态列。

## 安全
全程 **fail-open**：任何异常只写 debug 日志，绝不因账本写入影响下单/平仓。
开关 `LEARNING_LEDGER_TRADE_EVENTS`（**默认 true**）；回滚 = false。
"""
from __future__ import annotations

import logging
import time
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return str(os.getenv("LEARNING_LEDGER_TRADE_EVENTS", "true")).strip().lower() in (
        "1", "true", "yes", "on",
    )


def trade_lineage_id(account_id: int, position_id: Optional[int]) -> str:
    """确定性血缘 ID：同一笔持仓的开/平仓事件共享它。"""
    return f"lin_trade_{int(account_id)}_{int(position_id or 0)}"


def record_decision(*, symbol: str, action: str, lane: str = "", tier: str = "",
                    confidence: Optional[float] = None, executed: bool = True,
                    trace_id: str = "", code_reason: str = "",
                    account_id: Optional[int] = None) -> bool:
    """把一个**真实决策**（不是自测/回测）写进血缘账本。

    [2026-10-03 用户指令「补齐」· 面板①"决策→血缘账本 停滞"根因]
    实测账本里只有 7 条，来源全是 `selftest` / `backtest_loop` / `signal_backtest` /
    我自己验证用的 `paper_engine` ⇒ **生产决策没有任何入账通路**（面板文案"仅自测/回测
    来源入库，真实交易决策未入账（设计缺陷，待补）"）。
    这里补上 `source="live_decision"` 的通路：只记**真正执行**（executed=True）的决策，
    避免把每轮 9,800 条 hold 灌进账本；id 用 `lin_dec_{trace_id}` 保证幂等。
    """
    if not _enabled():
        return False
    try:
        from backend.services.learning_core import orchestrator
        from backend.services.learning_core.envelope import EvolutionEnvelope

        _tid = str(trace_id or "").strip() or f"{symbol}_{action}_{int(time.time())}"
        env = EvolutionEnvelope.root(
            stage="observe",
            source="live_decision",
            symbol=str(symbol).upper() if symbol else None,
            payload={
                "origin": "production_decision",
                "action": str(action or ""),
                "lane": str(lane or ""),
                "tier": str(tier or ""),
                "confidence": confidence,
                "executed": bool(executed),
                "code_reason": str(code_reason or "")[:200],
                "account_id": int(account_id) if account_id else None,
            },
            metrics={"confidence": float(confidence or 0.0)},
            status="passed" if executed else "pending",
            lineage_id=f"lin_dec_{_tid}",
        )
        orchestrator.emit(env)
        return True
    except Exception as e:  # fail-open
        logger.debug("[LearningLedger] 决策事件写入跳过: %s", e)
        return False


def _emit(stage: str, status: str, *, account_id: int, position_id: Optional[int],
          symbol: str, side: str, payload: Dict[str, Any],
          metrics: Optional[Dict[str, Any]] = None) -> bool:
    if not _enabled():
        return False
    try:
        from backend.services.learning_core import orchestrator
        from backend.services.learning_core.envelope import EvolutionEnvelope

        env = EvolutionEnvelope.root(
            stage=stage,
            source="paper_engine",
            symbol=str(symbol).upper() if symbol else None,
            payload={
                "account_id": int(account_id),
                "position_id": int(position_id) if position_id else None,
                "side": side,
                "origin": "production_trade",
                **payload,
            },
            metrics=metrics or {},
            status=status,
            lineage_id=trade_lineage_id(account_id, position_id),
        )
        orchestrator.emit(env)
        return True
    except Exception as e:  # fail-open：账本问题绝不影响交易
        logger.debug("[LearningLedger] 交易事件写入跳过: %s", e)
        return False


def record_trade_open(*, account_id: int, position_id: Optional[int], symbol: str, side: str,
                      tier: str = "", strategy_id: str = "", entry_price: float = 0.0,
                      quantity: float = 0.0, leverage: float = 0.0,
                      add_type: str = "") -> bool:
    """开仓 → `observe`（真实决策落地）。"""
    return _emit(
        "observe", "pending",
        account_id=account_id, position_id=position_id, symbol=symbol, side=side,
        payload={
            "event": "open", "tier": tier or None, "strategy_id": strategy_id or None,
            "entry_price": float(entry_price or 0), "quantity": float(quantity or 0),
            "leverage": float(leverage or 0), "add_type": add_type or None,
        },
        metrics={"entry_price": float(entry_price or 0)},
    )


def record_trade_close(*, account_id: int, position_id: Optional[int], symbol: str, side: str,
                       pnl: float, pnl_pct: float = 0.0, close_reason: str = "",
                       tier: str = "", strategy_id: str = "", entry_price: float = 0.0,
                       exit_price: float = 0.0, hold_minutes: float = 0.0) -> bool:
    """平仓 → `feedback`（把真实结果反馈进学习链路；盈利 passed / 亏损 rejected）。"""
    return _emit(
        "feedback", "passed" if float(pnl or 0) >= 0 else "rejected",
        account_id=account_id, position_id=position_id, symbol=symbol, side=side,
        payload={
            "event": "close", "close_reason": close_reason or None, "tier": tier or None,
            "strategy_id": strategy_id or None, "entry_price": float(entry_price or 0),
            "exit_price": float(exit_price or 0), "hold_minutes": float(hold_minutes or 0),
        },
        metrics={
            "pnl": round(float(pnl or 0), 4),
            "pnl_pct": round(float(pnl_pct or 0), 6),
            "win": 1 if float(pnl or 0) > 0 else 0,
        },
    )

