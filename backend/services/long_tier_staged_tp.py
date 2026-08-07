"""D14 — long tier 分批战略 TP.

取代 long tier 单一 TP 点位，改为按浮盈百分比分档减仓 +
到最后一档后由 ATR trailing 接管剩余仓位。

口径:
    pnl_pct = (current_price - entry_price) / entry_price    # 对 buy 方向
              (entry_price - current_price) / entry_price    # 对 sell 方向
    不考虑杠杆（"战略" TP 应该独立于杠杆决策）

返回:
    StagedTpDecision 包含 action 与 reduce_ratio / new_sl 建议。

使用:
    调用方每个 tick 都可以调 check()，只要 long_tier_state 保持持久化，
    就不会重复触发同一档。状态键建议使用 f"{account_id}:{symbol}:{side}"。

本模块是**纯函数 + dataclass**，不持有状态（状态由调用方管理）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("long_tier_staged_tp")


@dataclass
class StagedTpState:
    """每个 long 持仓的独立状态（调用方持久化，例如放进 service 的 dict）."""
    triggered_stages: list[int] = field(default_factory=list)  # [0,1] 表示 TP1/TP2 已触发
    peak_pnl_pct: float = 0.0                                   # 峰值浮盈 %（给 trailing 用）
    trailing_active: bool = False
    trailing_sl_price: Optional[float] = None
    entry_price: float = 0.0  # 2026-04-27: 记录校准 entry，DCA 后均价变化时自动重置


@dataclass
class StagedTpDecision:
    """Check 的返回决策."""
    action: str                        # "hold" | "reduce" | "trailing_hit" | "trailing_update"
    stage_idx: Optional[int] = None    # 触发的是哪一档 (0=TP1, 1=TP2, ...)
    reduce_ratio: float = 0.0          # 建议减仓比例
    reason: str = ""
    suggested_sl_price: Optional[float] = None  # trailing 更新时给出新的 SL


def _pnl_pct(entry_price: float, current_price: float, side: str) -> float:
    if entry_price <= 0 or current_price <= 0:
        return 0.0
    if side.lower() in ("buy", "long"):
        return (current_price - entry_price) / entry_price
    return (entry_price - current_price) / entry_price


def check(
    *,
    entry_price: float,
    current_price: float,
    side: str,
    atr_pct: float,
    state: StagedTpState,
) -> StagedTpDecision:
    """核心分档检查 — 纯函数.

    Args:
        entry_price:   开仓价
        current_price: 当前价
        side:          "buy"/"sell" 或 "long"/"short"
        atr_pct:       当前 ATR / price（1h 或 4h 都可，用于 trailing）
        state:         该持仓的 StagedTpState（调用方持久化）

    Returns:
        StagedTpDecision，action 可能是 hold/reduce/trailing_hit/trailing_update
    """
    from backend.config.settings import LONG_TIER_STAGED_TP, RISK_USE_LONG_TIER_STAGED_TP

    if not RISK_USE_LONG_TIER_STAGED_TP:
        return StagedTpDecision(action="hold", reason="flag_off")

    # 2026-04-27: DCA/金字塔加仓后均价变化 → 重置分档状态
    # 避免以旧均价触发的 stage 标记阻碍新均价下的正确触发
    if state.entry_price > 0 and abs(entry_price - state.entry_price) > 1e-8:
        logger.info(
            f"[StagedTP] entry_price changed {state.entry_price:.4f}→{entry_price:.4f}, resetting"
        )
        state.triggered_stages.clear()
        state.peak_pnl_pct = 0.0
        state.trailing_active = False
        state.trailing_sl_price = None
    state.entry_price = entry_price

    pnl = _pnl_pct(entry_price, current_price, side)

    # 更新峰值（后面 trailing 用）
    if pnl > state.peak_pnl_pct:
        state.peak_pnl_pct = pnl

    stages = LONG_TIER_STAGED_TP.get("stages", [])
    trailing_cfg = LONG_TIER_STAGED_TP.get("trailing_after_final_stage", {})

    # 依次检查未触发的档
    for idx, stage in enumerate(stages):
        if idx in state.triggered_stages:
            continue
        if pnl >= float(stage["trigger_pnl_pct"]):
            state.triggered_stages.append(idx)
            ratio = float(stage["exit_ratio"])
            return StagedTpDecision(
                action="reduce",
                stage_idx=idx,
                reduce_ratio=ratio,
                reason=f"tp_stage_{idx + 1}_pnl={pnl:.3f}>=trigger={stage['trigger_pnl_pct']}",
            )

    # 最后一档是否已触发 → trailing 接管
    if stages and len(state.triggered_stages) >= len(stages):
        activate = float(trailing_cfg.get("activate_after_pnl_pct", 0.25))
        if pnl >= activate or state.trailing_active:
            state.trailing_active = True
            atr_mult = float(trailing_cfg.get("atr_mult", 2.0))
            # trailing SL = peak 回撤 atr_mult × ATR_pct
            band = max(0.003, atr_pct * atr_mult)  # 最小 0.3% 保护
            peak_price = entry_price * (1 + state.peak_pnl_pct) if side.lower() in ("buy", "long") \
                else entry_price * (1 - state.peak_pnl_pct)
            if side.lower() in ("buy", "long"):
                new_sl = peak_price * (1 - band)
                if current_price <= new_sl:
                    return StagedTpDecision(
                        action="trailing_hit",
                        reason=f"trailing_sl_hit price={current_price:.6f}<=sl={new_sl:.6f}",
                    )
                return StagedTpDecision(
                    action="trailing_update",
                    suggested_sl_price=round(new_sl, 6),
                    reason=f"trailing_update peak={state.peak_pnl_pct:.3f} sl={new_sl:.6f}",
                )
            else:
                new_sl = peak_price * (1 + band)
                if current_price >= new_sl:
                    return StagedTpDecision(
                        action="trailing_hit",
                        reason=f"trailing_sl_hit price={current_price:.6f}>=sl={new_sl:.6f}",
                    )
                return StagedTpDecision(
                    action="trailing_update",
                    suggested_sl_price=round(new_sl, 6),
                    reason=f"trailing_update peak={state.peak_pnl_pct:.3f} sl={new_sl:.6f}",
                )

    return StagedTpDecision(action="hold", reason=f"pnl={pnl:.3f} no_stage_hit")
