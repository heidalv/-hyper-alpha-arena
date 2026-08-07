"""
DeterministicRiskGate — 确定性风控层

关键红线不由 LLM prompt 单独承担。所有开仓意图先过本模块，
被拒绝的决策写入 event_log 供事后审计。

规则:
  1. 单品种最大名义占总权益比 (max_symbol_notional_pct)
  2. 单侧（多/空）最大保证金占总权益比 (max_side_margin_pct)
  3. 单日最大已实现亏损 (max_daily_loss_pct)
  4. 全组合最大杠杆 (max_portfolio_leverage)
  5. 最小可用余额 (min_available_balance_pct)
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from backend.services.strategy_params_registry import DEFAULT_RULES

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    passed: bool
    reason_code: str = ""        # 通过时为空
    reason_text: str = ""        # 人类可读
    blocked_by: str = ""         # 触发的规则名称


@dataclass
class AccountSnapshot:
    total_equity: float
    available_balance: float
    frozen_margin: float
    realized_pnl_today: float = 0.0


@dataclass
class PositionInfo:
    symbol: str
    side: str           # long / short
    margin: float
    notional: float
    size: float
    leverage: float


@dataclass
class ProposedOrder:
    symbol: str
    side: str           # buy / sell
    notional: float
    margin: float
    leverage: float


# ════════════════════════════════════════════════════════
#  默认规则参数 — 从统一注册表导入，可通过 init 覆盖
# ════════════════════════════════════════════════════════

# DEFAULT_RULES 从 strategy_params_registry 导入（见文件顶部 import）


class DeterministicRiskGate:
    """无状态风控检查器 — 每次调用传入当前快照即可"""

    def __init__(self, rules: Optional[Dict[str, float]] = None):
        self.rules = {**DEFAULT_RULES, **(rules or {})}

    def check(
        self,
        account: AccountSnapshot,
        positions: List[PositionInfo],
        order: ProposedOrder,
        symbol_daily_pnl: Optional[float] = None,
    ) -> RiskCheckResult:
        """执行所有规则，返回第一个不通过的原因；全部通过则 passed=True"""

        equity = account.total_equity or 1.0  # 防除零

        # ── Rule 1: 单品种保证金占比（全账户统一用 margin 基准）──
        #
        # 2026-05-08 深挖第 4 轮 修复：
        # 原逻辑对 equity ≥ $500 的账户用 notional 基准，notional = margin × leverage
        # 这意味着 25% notional 对应 5x 杠杆下仅 5% margin、10x 下 2.5% margin —
        # 几乎所有 AI 决策都被拦死（实测 100% 拦截率）。
        #
        # 修正：
        #   - 全账户统一用 margin 基准（与 max_side_margin_pct / max_position_per_trade_ratio 一致）
        #   - 名义敞口由 max_portfolio_leverage（Rule 4）独立约束
        #   - 默认 25% margin 在 10x 杠杆下等价 250% notional —— 杠杆账户的合理上限
        max_sym = self.rules["max_symbol_notional_pct"]
        existing_sym_exposure = 0.0
        if order.side == "buy":
            existing_sym_exposure = sum(p.margin for p in positions
                                        if p.symbol == order.symbol and p.side == "long")
        elif order.side == "sell":
            existing_sym_exposure = sum(p.margin for p in positions
                                        if p.symbol == order.symbol and p.side == "short")
        new_sym_exposure = existing_sym_exposure + order.margin

        if new_sym_exposure / equity > max_sym:
            return RiskCheckResult(
                passed=False,
                reason_code="symbol_margin_exceeded",
                reason_text=f"品种 {order.symbol} 保证金 {new_sym_exposure:.0f} > {max_sym:.0%}×{equity:.0f}",
                blocked_by="max_symbol_notional_pct",
            )

        # ── Rule 2: 单侧保证金占比 ──
        max_side = self.rules["max_side_margin_pct"]
        side = "long" if order.side == "buy" else "short"
        existing_side_margin = sum(p.margin for p in positions if p.side == side)
        new_side_margin = existing_side_margin + order.margin
        if new_side_margin / equity > max_side:
            return RiskCheckResult(
                passed=False,
                reason_code="side_margin_exceeded",
                reason_text=f"{side} 侧保证金 {new_side_margin:.0f} > {max_side:.0%}×{equity:.0f}",
                blocked_by="max_side_margin_pct",
            )

        # ── Rule 3: 单日亏损熔断 ──
        # 3a: per-symbol 检查（当传入 symbol_daily_pnl 时）
        if symbol_daily_pnl is not None and symbol_daily_pnl < 0:
            sym_loss_pct = abs(symbol_daily_pnl) / equity
            max_sym_daily = self.rules.get("max_symbol_daily_loss_pct", 3.0) / 100.0
            if sym_loss_pct >= max_sym_daily:
                return RiskCheckResult(
                    passed=False,
                    reason_code="symbol_daily_loss_circuit",
                    reason_text=f"{order.symbol} 当日已亏 {sym_loss_pct:.1%} ≥ {max_sym_daily:.0%} symbol熔断线",
                    blocked_by="max_symbol_daily_loss_pct",
                )
        # 3b: 全局日亏损检查（阈值提高到极端安全网级别，per-symbol 由上层处理）
        max_daily = self.rules.get("global_extreme_daily_loss_pct", 15.0) / 100.0
        if account.realized_pnl_today < 0:
            daily_loss_pct = abs(account.realized_pnl_today) / equity
            if daily_loss_pct >= max_daily:
                return RiskCheckResult(
                    passed=False,
                    reason_code="daily_loss_circuit",
                    reason_text=f"当日已亏 {daily_loss_pct:.1%} ≥ {max_daily:.0%} 极端安全网",
                    blocked_by="global_extreme_daily_loss_pct",
                )

        # ── Rule 4: 组合最大杠杆 ──
        max_lev = self.rules["max_portfolio_leverage"]
        if order.leverage > max_lev:
            return RiskCheckResult(
                passed=False,
                reason_code="leverage_exceeded",
                reason_text=f"杠杆 {order.leverage}x > 最大 {max_lev}x",
                blocked_by="max_portfolio_leverage",
            )

        # ── Rule 5: 最小可用余额 ──
        # 小资金账户（<$200）放宽到 5%，避免 $100 账户几乎无法开仓
        min_avail_pct = self.rules["min_available_balance_pct"]
        if equity < 200:
            min_avail_pct = min(min_avail_pct, 0.05)
        projected_avail = account.available_balance - order.margin
        if projected_avail / equity < min_avail_pct:
            return RiskCheckResult(
                passed=False,
                reason_code="available_balance_low",
                reason_text=f"开仓后可用 {projected_avail:.0f} < {min_avail_pct:.0%}×{equity:.0f}",
                blocked_by="min_available_balance_pct",
            )

        return RiskCheckResult(passed=True)


# 单例
risk_gate = DeterministicRiskGate()
