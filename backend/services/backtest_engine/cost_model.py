"""
统一成本模型 — 回测与实盘使用完全相同的费率/滑点参数

参数来源:
  - Hyperliquid Maker: 0.02% (0.0002)
  - Hyperliquid Taker: 0.035% (0.00035)
  - 滑点模型: 与 fee_guard.py 的 calc_slippage_rate() 对齐
  - 资金费率: 每8小时结算，默认 0.01%
"""

from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────
# Hyperliquid 真实费率常量（唯一真相源）
# ─────────────────────────────────────────────
TAKER_FEE: float = 0.00035    # 0.035%
MAKER_FEE: float = 0.0002     # 0.02%

# 滑点分级参数（与 fee_guard.py 完全对齐）
SLIPPAGE_BASE: float = 0.0005   # 0.05% 基础滑点
SLIPPAGE_MAX: float = 0.003     # 0.30% 上限
SL_SLIPPAGE_MULT: float = 2.0   # 止损滑点乘数

# 规模分档（名义价值 USD）→ 额外滑点
_SIZE_TIERS = [
    (100_000, 0.0004),   # > $100k
    (20_000,  0.0002),   # $20k - $100k
    (5_000,   0.0001),   # $5k - $20k
    (0,       0.0),      # < $5k
]

# 子仓类型调整
_NATURE_ADJ = {
    "trend_follow": -0.0001,
    "swing":         0.0,
    "intraday":     +0.0002,
    "scalp":        +0.0003,
}

# 资金费率默认值（每8小时）
DEFAULT_FUNDING_RATE: float = 0.0001   # 0.01%


@dataclass
class CostModel:
    """统一交易成本模型 — 回测与实盘共用"""

    taker_fee: float = TAKER_FEE
    maker_fee: float = MAKER_FEE
    base_slippage: float = SLIPPAGE_BASE
    sl_slippage_mult: float = SL_SLIPPAGE_MULT
    funding_rate: float = DEFAULT_FUNDING_RATE

    def calc_slippage_rate(
        self,
        notional_usd: float,
        trade_nature: str = "swing",
        is_sl: bool = False,
    ) -> float:
        """计算综合滑点率（单边），与 fee_guard.calc_slippage_rate 对齐。

        Args:
            notional_usd: 订单名义价值 (USD)
            trade_nature: 子仓类型 trend_follow / swing / intraday / scalp
            is_sl: 是否止损触发

        Returns:
            单边滑点率 (例如 0.0008 = 0.08%)

        2026-07-09 P0-4 统一：本类此前是 fee_guard 的平行副本（口径分叉来源），
        现改为直接委托实盘单一真相源 fee_guard.calc_slippage_rate，杜绝分叉。
        注：本 dataclass 的 base_slippage/sl_slippage_mult 字段对滑点结果不再生效
        （口径以 fee_guard 常量为准）；保留字段仅为兼容既有构造签名。
        """
        from backend.services.fee_guard import calc_slippage_rate as _fg_slip
        return _fg_slip(notional_usd, trade_nature, is_sl=is_sl)

    def calc_round_trip_cost(
        self,
        notional_usd: float,
        is_maker: bool = False,
        trade_nature: str = "swing",
        is_sl: bool = False,
    ) -> float:
        """计算往返（开+平）总成本率。

        Returns:
            往返总成本率 (如 0.0017 = 0.17%)
        """
        fee_rate = self.maker_fee if is_maker else self.taker_fee
        slippage = self.calc_slippage_rate(notional_usd, trade_nature, is_sl)
        # 往返: 2 × (手续费 + 滑点)
        return (fee_rate + slippage) * 2

    def calc_funding_cost(
        self,
        position_value: float,
        hours_held: float,
        funding_rate: Optional[float] = None,
    ) -> float:
        """计算持仓期间的累计资金费率成本。

        Hyperliquid 每 8 小时结算一次。

        Returns:
            总资金费率成本 (USD)
        """
        fr = funding_rate if funding_rate is not None else self.funding_rate
        periods = hours_held / 8.0
        return position_value * abs(fr) * periods

    def apply_to_pnl(
        self,
        entry_notional: float,
        exit_notional: float,
        raw_pnl_pct: float,
        is_sl: bool = False,
        trade_nature: str = "swing",
        hours_held: float = 0.0,
    ) -> tuple:
        """将成本从原始 PnL 中扣除。

        Returns:
            (net_pnl_pct, cost_details_dict)
        """
        avg_notional = (entry_notional + exit_notional) / 2
        fee_rate = self.taker_fee  # 保守按 taker
        open_slip = self.calc_slippage_rate(entry_notional, trade_nature, is_sl=False)
        close_slip = self.calc_slippage_rate(exit_notional, trade_nature, is_sl=is_sl)

        total_cost_pct = (fee_rate + open_slip) + (fee_rate + close_slip)

        # 资金费率
        funding_pct = 0.0
        if hours_held > 0:
            funding_usd = self.calc_funding_cost(avg_notional, hours_held)
            funding_pct = funding_usd / avg_notional if avg_notional > 0 else 0.0

        total_cost_pct += funding_pct
        net_pnl = raw_pnl_pct - total_cost_pct

        return net_pnl, {
            "open_cost_pct": fee_rate + open_slip,
            "close_cost_pct": fee_rate + close_slip,
            "funding_cost_pct": funding_pct,
            "total_cost_pct": total_cost_pct,
        }
