"""
手续费 + 滑点门卫 — 综合交易成本（手续费 + 滑点）不覆盖预期利润时拒绝执行。

Hyperliquid 费率:
  Maker  0.02%  (0.0002)
  Taker  0.035% (0.00035)

滑点分级 (由 _calc_slippage 动态计算，也可外部传入 slippage_rate 覆盖):
  普通行情基础: 0.05%
  大单/高波动: 最高 0.30%
  止损触发:   额外 2x 乘数（市场快速运动时滑点更大）

规则: 预期利润 >= MIN_PROFIT_RATIO * (往返手续费 + 往返滑点成本)
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 滑点分级参数
# ─────────────────────────────────────────────
SLIPPAGE_BASE   = 0.0005   # 0.05% 基础滑点（所有订单）
SLIPPAGE_MAX    = 0.003    # 0.30% 上限，防止极端情况失真
SL_SLIP_MULT    = 2.0      # 止损触发时滑点乘数（快市价格跳空）

# 规模分档（名义价值 USD）→ 额外滑点
_SIZE_TIERS = [
    (100_000, 0.0004),   # > $100k
    (20_000,  0.0002),   # $20k - $100k
    (5_000,   0.0001),   # $5k - $20k
    (0,       0.0),      # < $5k
]

# 子仓类型调整（贴近实际交易习惯）
_NATURE_ADJ = {
    "trend_follow": -0.0001,   # 长线单可等候好价位，冲击较小
    "swing":         0.0,
    "intraday":     +0.0002,   # 短线频繁交易，点差敏感
}


def calc_slippage_rate(
    notional_usd: float,
    trade_nature: str = "swing",
    is_sl: bool = False,
) -> float:
    """计算综合滑点率（单边）。

    Args:
        notional_usd: 订单名义价值 (USD)
        trade_nature: 子仓类型 trend_follow / swing / intraday
        is_sl:        是否止损触发（快市额外放大）

    Returns:
        单边滑点率 (例如 0.0008 = 0.08%)
    """
    # 基础滑点
    rate = SLIPPAGE_BASE

    # 规模分档
    for threshold, extra in _SIZE_TIERS:
        if notional_usd > threshold:
            rate += extra
            break

    # 子仓类型修正
    rate += _NATURE_ADJ.get(trade_nature, 0.0)

    # 止损场景：价格往往已大幅偏离触发价
    if is_sl:
        rate *= SL_SLIP_MULT

    return min(max(rate, 0.0), SLIPPAGE_MAX)


class FeeGuard:
    MAKER_RATE = 0.0002
    TAKER_RATE = 0.00035
    MIN_PROFIT_FEE_RATIO = 3.0

    @staticmethod
    def _resolve_fee_rate(is_maker: bool, exchange: Optional[str] = None) -> float:
        """按实际交易所解析费率；失败时降级到硬编码 hyperliquid 值。

        修复（2026-06-24）：原 FeeGuard 硬编码 MAKER_RATE/TAKER_RATE（hyperliquid
        0.02%/0.035%），但账户可能用 asterdex（0.005%）或 binance（0.04%）。
        导致：asterdex 账户的成本估算高估 7 倍（过度保守），且不能反映真实费率。
        现改为优先调 fee_schedule_service.get_fee_rate(exchange) 取真实费率。
        """
        if exchange:
            try:
                from backend.services.fee_schedule_service import get_fee_rate
                return get_fee_rate(exchange, is_maker)
            except Exception:
                pass
        return FeeGuard.MAKER_RATE if is_maker else FeeGuard.TAKER_RATE

    def check_open(
        self,
        notional_usd: float,
        tp_pct: float,
        is_maker: bool = False,
        slippage_rate: Optional[float] = None,
        trade_nature: str = "swing",
        exchange: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """检查开仓是否值得: 预期利润 vs 往返（手续费 + 滑点）。

        Args:
            notional_usd:   名义价值 (USD)
            tp_pct:         预期止盈百分比 (0.06 = 6%)
            is_maker:       是否限价单（maker 费率更低）
            slippage_rate:  单边滑点率，None 时自动按规模计算
            trade_nature:   子仓类型，用于自动计算滑点
            exchange:       交易所名（None 则用硬编码 hyperliquid 费率兜底）。
                            指定后按该交易所真实费率评估，避免成本高估/低估。

        Returns:
            (通过, 原因说明)
        """
        if notional_usd <= 0 or tp_pct <= 0:
            return False, f"notional={notional_usd:.2f} tp_pct={tp_pct:.4f} invalid"

        fee_rate = self._resolve_fee_rate(is_maker, exchange)

        # 自动计算或使用外部传入的滑点率
        slip = slippage_rate if slippage_rate is not None else calc_slippage_rate(
            notional_usd, trade_nature
        )

        # 往返总成本 = 2 * (手续费 + 滑点) * notional
        round_trip_cost = notional_usd * (fee_rate + slip) * 2
        expected_profit = notional_usd * tp_pct

        if expected_profit < round_trip_cost * self.MIN_PROFIT_FEE_RATIO:
            reason = (
                f"预期利润${expected_profit:.2f} < "
                f"{self.MIN_PROFIT_FEE_RATIO:.0f}x综合成本"
                f"${round_trip_cost * self.MIN_PROFIT_FEE_RATIO:.2f}"
                f" (fee={fee_rate*100:.3f}% slip={slip*100:.3f}%)"
            )
            logger.info(f"[FeeGuard] 拦截: {reason}")
            return False, reason

        return True, "ok"

    def check_reduce(
        self,
        reduce_notional: float,
        current_pnl_usd: float,
        is_maker: bool = False,
        slippage_rate: Optional[float] = None,
        trade_nature: str = "swing",
        is_sl: bool = False,
        exchange: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """检查减仓是否值得: 浮盈 vs (平仓手续费 + 滑点)。

        Args:
            reduce_notional:  减仓部分的名义价值 (USD)
            current_pnl_usd:  减仓部分当前浮盈 (USD)
            is_maker:         是否限价单
            slippage_rate:    单边滑点率，None 时自动计算
            trade_nature:     子仓类型
            is_sl:            是否止损场景（滑点加倍）
            exchange:         交易所名（None 则用硬编码费率兜底）

        Returns:
            (通过, 原因说明)
        """
        if reduce_notional <= 0:
            return False, "reduce_notional <= 0"

        fee_rate = self._resolve_fee_rate(is_maker, exchange)
        slip = slippage_rate if slippage_rate is not None else calc_slippage_rate(
            reduce_notional, trade_nature, is_sl=is_sl
        )

        # 平仓单次成本（只平，不含开仓那边）
        close_cost = reduce_notional * (fee_rate + slip)
        min_profit = close_cost * self.MIN_PROFIT_FEE_RATIO

        if current_pnl_usd > 0 and current_pnl_usd < min_profit:
            reason = (
                f"减仓浮盈${current_pnl_usd:.2f} < "
                f"{self.MIN_PROFIT_FEE_RATIO:.0f}x综合成本"
                f"${min_profit:.2f}"
                f" (fee={fee_rate*100:.3f}% slip={slip*100:.3f}%)"
            )
            logger.info(f"[FeeGuard] 减仓拦截: {reason}")
            return False, reason

        return True, "ok"

    def estimate_breakeven_move(
        self,
        notional_usd: float,
        is_maker: bool = False,
        trade_nature: str = "swing",
        exchange: Optional[str] = None,
    ) -> float:
        """计算盈亏平衡所需的最小价格变动百分比（含往返手续费+滑点）。

        Returns:
            最小价格变动率 (例如 0.0018 = 0.18%)
        """
        fee_rate = self._resolve_fee_rate(is_maker, exchange)
        slip = calc_slippage_rate(notional_usd, trade_nature)
        # 往返成本 = 2 * (手续费率 + 滑点率)
        return (fee_rate + slip) * 2


fee_guard = FeeGuard()
