"""Paper delta-neutral 双腿准原子执行 + 回滚 + delta 漂移监控 + 完整成本模型（Phase 3）。

2026-07-06 新增（修复病灶F：单腿裸敞口 / 非原子）：
    delta-neutral 刷积分/资金费套利必须"两腿都在才中性"。本执行器在 Paper 模式下把
    双腿开仓做成**准原子**：

        1. 先成交长腿（积分DEX 做多）；
        2. 再成交空腿（深场所 做空，等额名义）；
        3. 若空腿失败 → **回滚长腿**（模拟平掉），不留下单腿裸敞口；
        4. 两腿都成后计算 **delta 漂移**（净敞口/名义），超阈值告警；
        5. 输出**完整成本模型**：两腿开+平手续费、滑点、持有期资金费净收益。

纯 Paper：只有"成交"是模拟，价格/费率走真实快照（rebate_paper_market + program_registry）。
可注入 quote_resolver 便于单测；离线拿不到行情时该腿判为不可成交（不臆造成交价）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# delta 漂移告警阈值：净敞口 / 单腿名义 超过 2% 视为偏离中性。
DEFAULT_DELTA_DRIFT_THRESHOLD_PCT = 0.02


@dataclass
class LegFill:
    """单腿成交结果。"""

    exchange: str
    symbol: str
    side: str                 # buy / sell
    notional_usd: float
    fill_price: float = 0.0
    fee_usd: float = 0.0
    filled: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "side": self.side,
            "notional_usd": round(self.notional_usd, 2),
            "fill_price": self.fill_price,
            "fee_usd": round(self.fee_usd, 4),
            "filled": self.filled,
            "reason": self.reason,
        }


@dataclass
class DeltaNeutralExecResult:
    """双腿执行结果（含回滚状态、delta 漂移、成本模型）。"""

    success: bool
    rolled_back: bool = False
    legs: List[LegFill] = field(default_factory=list)
    net_delta_usd: float = 0.0
    delta_drift_pct: float = 0.0
    delta_drift_alert: bool = False
    cost_model: Dict[str, float] = field(default_factory=dict)
    reason: str = ""
    paper_mode: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "rolled_back": self.rolled_back,
            "legs": [l.to_dict() for l in self.legs],
            "net_delta_usd": round(self.net_delta_usd, 4),
            "delta_drift_pct": round(self.delta_drift_pct, 6),
            "delta_drift_alert": self.delta_drift_alert,
            "cost_model": {k: round(v, 6) for k, v in self.cost_model.items()},
            "reason": self.reason,
            "paper_mode": self.paper_mode,
        }


class PaperDeltaNeutralExecutor:
    """Paper delta-neutral 双腿执行器。"""

    def __init__(
        self,
        quote_resolver: Optional[Callable[[str, str], Any]] = None,
        delta_drift_threshold_pct: float = DEFAULT_DELTA_DRIFT_THRESHOLD_PCT,
    ):
        self._quote_resolver = quote_resolver
        self.delta_drift_threshold_pct = delta_drift_threshold_pct

    # ── 依赖解析 ──
    def _resolve_quote(self, exchange: str, symbol: str):
        """取某场所某 symbol 的 Paper 行情（可注入；默认走 rebate_paper_market）。"""
        if self._quote_resolver is not None:
            return self._quote_resolver(exchange, symbol)
        try:
            from backend.services.rebate_arb.rebate_paper_market import resolve_paper_market

            return resolve_paper_market(symbol, exchange)
        except Exception as exc:
            logger.debug("[PaperDN] 行情解析失败 %s/%s: %s", exchange, symbol, exc)
            return None

    def _fee_rate(self, exchange: str, taker: bool) -> float:
        try:
            from backend.services.rebate_arb import program_registry as pr

            fees = pr.get_offline_incentive(exchange)
            return fees.get("taker_rate" if taker else "maker_rate", 0.0005 if taker else 0.0002)
        except Exception:
            return 0.0005 if taker else 0.0002

    def _fill_leg(self, leg: Dict[str, Any], notional_usd: float, taker: bool) -> LegFill:
        """模拟成交单腿：买用 ask、卖用 bid（体现滑点），费用按场所费率。"""
        exchange = leg.get("exchange", "")
        symbol = leg.get("symbol", "")
        side = leg.get("side", "buy")
        fill = LegFill(exchange=exchange, symbol=symbol, side=side, notional_usd=notional_usd)

        quote = self._resolve_quote(exchange, symbol)
        if quote is None or getattr(quote, "mid", 0) <= 0:
            fill.filled = False
            fill.reason = "无行情，不可成交（拒绝臆造成交价）"
            return fill

        if side == "buy":
            fill.fill_price = float(getattr(quote, "ask", None) or quote.mid)
        else:
            fill.fill_price = float(getattr(quote, "bid", None) or quote.mid)

        fill.fee_usd = notional_usd * self._fee_rate(exchange, taker)
        fill.filled = True
        fill.reason = "paper_filled"
        return fill

    def _rollback_leg(self, filled: LegFill, taker: bool) -> LegFill:
        """模拟平掉已成交的长腿（回滚），产生一次平仓手续费。"""
        opposite = "sell" if filled.side == "buy" else "buy"
        close = LegFill(
            exchange=filled.exchange,
            symbol=filled.symbol,
            side=opposite,
            notional_usd=filled.notional_usd,
        )
        quote = self._resolve_quote(filled.exchange, filled.symbol)
        if quote is not None and getattr(quote, "mid", 0) > 0:
            close.fill_price = float(
                (getattr(quote, "bid", None) or quote.mid)
                if opposite == "sell"
                else (getattr(quote, "ask", None) or quote.mid)
            )
        close.fee_usd = filled.notional_usd * self._fee_rate(filled.exchange, taker)
        close.filled = True
        close.reason = "rollback_close"
        return close

    def compute_delta_drift(self, legs: List[LegFill]) -> Dict[str, float]:
        """净敞口 = Σ(多腿名义) - Σ(空腿名义)；漂移 = |净敞口| / 单腿名义。"""
        long_notional = sum(l.notional_usd for l in legs if l.filled and l.side == "buy")
        short_notional = sum(l.notional_usd for l in legs if l.filled and l.side == "sell")
        base = max(long_notional, short_notional, 1e-9)
        net = long_notional - short_notional
        return {
            "net_delta_usd": net,
            "delta_drift_pct": abs(net) / base,
        }

    def execute(
        self,
        plan: Dict[str, Any],
        notional_usd: float,
        *,
        taker: bool = True,
        combo: Optional[Dict[str, Any]] = None,
        horizon_days: float = 7.0,
    ) -> DeltaNeutralExecResult:
        """准原子双腿开仓：长腿成 → 空腿成；空腿失败则回滚长腿。"""
        side_a = plan.get("side_a") or {}
        side_b = plan.get("side_b") or {}
        if not side_a or not side_b:
            return DeltaNeutralExecResult(
                success=False,
                reason="计划缺少双腿（delta-neutral 必须两腿）",
            )

        # 1) 长腿
        leg_a = self._fill_leg(side_a, notional_usd, taker)
        if not leg_a.filled:
            return DeltaNeutralExecResult(
                success=False,
                legs=[leg_a],
                reason=f"长腿未成交（{leg_a.reason}），未开任何仓",
            )

        # 2) 空腿
        leg_b = self._fill_leg(side_b, notional_usd, taker)
        if not leg_b.filled:
            # 3) 回滚长腿，避免单腿裸敞口
            rollback = self._rollback_leg(leg_a, taker)
            return DeltaNeutralExecResult(
                success=False,
                rolled_back=True,
                legs=[leg_a, leg_b, rollback],
                reason=f"空腿未成交（{leg_b.reason}）→ 已回滚长腿，无裸敞口",
            )

        # 4) delta 漂移
        drift = self.compute_delta_drift([leg_a, leg_b])
        alert = drift["delta_drift_pct"] > self.delta_drift_threshold_pct

        # 5) 完整成本模型
        cost = self.compute_cost_model(
            [leg_a, leg_b], notional_usd, combo or {}, horizon_days, taker
        )

        if alert:
            logger.warning(
                "[PaperDN] delta 漂移 %.4f%% 超阈值 %.4f%%（净敞口 $%.2f）",
                drift["delta_drift_pct"] * 100,
                self.delta_drift_threshold_pct * 100,
                drift["net_delta_usd"],
            )

        return DeltaNeutralExecResult(
            success=True,
            legs=[leg_a, leg_b],
            net_delta_usd=drift["net_delta_usd"],
            delta_drift_pct=drift["delta_drift_pct"],
            delta_drift_alert=alert,
            cost_model=cost,
            reason="双腿准原子开仓成功，delta 中性",
        )

    def compute_cost_model(
        self,
        legs: List[LegFill],
        notional_usd: float,
        combo: Dict[str, Any],
        horizon_days: float,
        taker: bool,
    ) -> Dict[str, float]:
        """完整成本模型：开+平两腿手续费 + 滑点 + 持有期资金费净收益。"""
        entry_fee = sum(l.fee_usd for l in legs if l.filled)
        # 平仓费按同费率估计（两腿各一次）
        exit_fee = sum(
            l.notional_usd * self._fee_rate(l.exchange, taker) for l in legs if l.filled
        )
        # 滑点：买卖价差的一半，已隐含在 ask/bid 成交价里，这里额外记一档保守滑点估计
        slippage_est = notional_usd * 0.0002 * len(legs)

        # 持有期资金费净收益（来自 combo 的 net_funding_per_day）
        net_funding_per_day = float(combo.get("net_funding_per_day", 0.0)) if combo else 0.0
        funding_pnl = net_funding_per_day * horizon_days * notional_usd

        total_cost = entry_fee + exit_fee + slippage_est
        net_ev = funding_pnl - total_cost
        return {
            "entry_fee_usd": entry_fee,
            "exit_fee_usd": exit_fee,
            "slippage_est_usd": slippage_est,
            "funding_pnl_usd": funding_pnl,
            "total_cost_usd": total_cost,
            "net_ev_usd": net_ev,
        }


# 模块级单例（Paper 用；实盘执行走 Phase 5 另行接入）
paper_delta_neutral_executor = PaperDeltaNeutralExecutor()
