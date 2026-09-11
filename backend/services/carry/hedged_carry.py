# -*- coding: utf-8 -*-
"""[F70] 对冲 carry（现货多 + 永续空）的纸面执行通道与真实数据回测。

设计依据：《复合策略与交易系统全面改造设计_V2》§1.3 L2。

本模块补齐 L2 缺失的最后两块：
  1. **现货下单通道**（`SpotPaperChannel`）——按真实现货价成交、计 taker/maker 费率与滑点，
     维护现货余额与持仓；此前库里只有现货行情，没有任何可下单的现货通道。
  2. **对冲腿模拟**（`HedgedCarrySim` / `simulate_position`）——两条腿同时进出，
     收益 = 资金费收入 + 基差收敛 − 两条腿的手续费 − 滑点，**逐笔现金流**算，
     不靠「bp 近似」。

数据源（全部真实）：
  - 现货：`market_spot_klines`（Binance spot，5m，由 F64 采集）
  - 永续：`crypto_klines`（对应场地，5m；注意 `timestamp` 是**秒**）
  - 资金费：`perp_funding`（毫秒；采样 ~5min，结算按 8h/1h 边界取最近样本）

口径要点：
  - 两条腿名义相等（delta 中性），数量由**现货腿**的成交价确定；
  - 资金费按结算边界逐期计入（做空永续在费率为正时**收取**）；
  - 平仓时两条腿都用对手价（taker）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_SPOT_EXCHANGE = "binance_spot"
DEFAULT_SPOT_TAKER_BP = 10.0      # Binance 现货基础档 taker 0.10%
DEFAULT_SPOT_MAKER_BP = 10.0
DEFAULT_SPOT_SLIPPAGE_BP = 1.0
DEFAULT_PERP_TAKER_BP = 4.0       # Aster/Binance 永续 taker 0.04%
DEFAULT_PERP_SLIPPAGE_BP = 0.5


# ═══════════════════════ 现货纸面通道 ═══════════════════════

@dataclass
class SpotFill:
    symbol: str
    side: str          # buy / sell
    qty: float
    px: float
    fee_usd: float
    slippage_usd: float
    ts: float = 0.0

    @property
    def cash_usd(self) -> float:
        """现金变动（买为负、卖为正），已扣手续费与滑点。"""
        gross = -self.qty * self.px if self.side == "buy" else self.qty * self.px
        return gross - self.fee_usd - self.slippage_usd


class SpotPaperChannel:
    """纸面现货通道：真实价格成交，含费率与滑点，维护持仓与现金。

    为什么需要它：库里此前**没有现货下单通道**（设计 §1.3 列为待建），
    没有它就无法构建对冲腿、无法验证 carry 的真实成本。
    """

    def __init__(
        self,
        *,
        exchange: str = DEFAULT_SPOT_EXCHANGE,
        taker_bp: float = DEFAULT_SPOT_TAKER_BP,
        maker_bp: float = DEFAULT_SPOT_MAKER_BP,
        slippage_bp: float = DEFAULT_SPOT_SLIPPAGE_BP,
        initial_cash_usd: float = 0.0,
    ) -> None:
        self.exchange = exchange
        self.taker_bp = float(taker_bp)
        self.maker_bp = float(maker_bp)
        self.slippage_bp = float(slippage_bp)
        self.cash_usd = float(initial_cash_usd)
        self.positions: Dict[str, float] = {}
        self.avg_px: Dict[str, float] = {}
        self.fills: List[SpotFill] = []
        self.fee_paid_usd = 0.0
        self.slippage_paid_usd = 0.0

    def _execute(self, symbol: str, side: str, qty: float, px: float,
                 *, is_maker: bool = False, ts: float = 0.0) -> SpotFill:
        fee_bp = self.maker_bp if is_maker else self.taker_bp
        # 滑点方向对我不利：买更贵、卖更便宜
        slip = self.slippage_bp / 1e4 * px * (1.0 if side == "buy" else -1.0)
        exec_px = px + slip
        notional = abs(qty * exec_px)
        fee = abs(fee_bp) / 1e4 * notional
        slip_cost = abs(slip * qty)
        fill = SpotFill(symbol=symbol, side=side, qty=float(qty), px=float(exec_px),
                        fee_usd=round(fee, 8), slippage_usd=round(slip_cost, 8), ts=ts)
        self.fills.append(fill)
        self.fee_paid_usd += fee
        self.slippage_paid_usd += slip_cost
        self.cash_usd += fill.cash_usd

        cur = float(self.positions.get(symbol, 0.0))
        signed = float(qty) if side == "buy" else -float(qty)
        new = cur + signed
        if abs(cur) < 1e-12:
            self.avg_px[symbol] = exec_px
        elif cur * signed > 0:
            tot = abs(cur) + abs(signed)
            self.avg_px[symbol] = (self.avg_px.get(symbol, exec_px) * abs(cur)
                                   + exec_px * abs(signed)) / tot
        else:
            if abs(new) < 1e-12:
                self.avg_px[symbol] = 0.0
            elif new * cur < 0:
                self.avg_px[symbol] = exec_px
        self.positions[symbol] = new
        return fill

    def buy(self, symbol: str, qty: float, px: float, **kw) -> SpotFill:
        return self._execute(symbol, "buy", qty, px, **kw)

    def sell(self, symbol: str, qty: float, px: float, **kw) -> SpotFill:
        return self._execute(symbol, "sell", qty, px, **kw)

    def mark_to_market_usd(self, marks: Dict[str, float]) -> float:
        return sum(q * float(marks.get(s, 0.0) or 0.0)
                   for s, q in self.positions.items())

    def equity_usd(self, marks: Dict[str, float]) -> float:
        return self.cash_usd + self.mark_to_market_usd(marks)


# ═══════════════════════ 对冲腿模拟 ═══════════════════════

@dataclass
class HedgedCarrySim:
    """一笔对冲 carry：现货多 + 永续空（费率为正时收资金费）。

    逐笔现金流口径：
      现货腿：买入付 `qty×S0×(1+spot_fee+slip)`，卖出收 `qty×S1×(1−spot_fee−slip)`
      永续腿：做空收 `qty×P0×(1−perp_fee−slip)`，买回付 `qty×P1×(1+perp_fee+slip)`
      资金费：每期 `+qty×P_i×rate_i`（rate>0 时空头收取）
    净额 = 三者之和。
    """

    symbol: str
    venue: str
    notional_usd: float
    qty: float
    entry_spot_px: float
    entry_perp_px: float
    entry_ts: float
    spot_taker_bp: float = DEFAULT_SPOT_TAKER_BP
    perp_taker_bp: float = DEFAULT_PERP_TAKER_BP
    spot_slippage_bp: float = DEFAULT_SPOT_SLIPPAGE_BP
    perp_slippage_bp: float = DEFAULT_PERP_SLIPPAGE_BP
    funding_periods: int = 0
    funding_usd: float = 0.0
    exit_spot_px: float = 0.0
    exit_perp_px: float = 0.0
    exit_ts: float = 0.0
    spot_fee_usd: float = 0.0
    perp_fee_usd: float = 0.0
    slippage_usd: float = 0.0
    closed: bool = False

    # ── 进出场 ──
    def enter(self) -> None:
        """建仓：两腿按各自成交价、含费率与滑点。"""
        s_fee = self.spot_taker_bp / 1e4
        p_fee = self.perp_taker_bp / 1e4
        s_slip = self.spot_slippage_bp / 1e4
        p_slip = self.perp_slippage_bp / 1e4
        self.spot_fee_usd += self.qty * self.entry_spot_px * s_fee
        self.perp_fee_usd += self.qty * self.entry_perp_px * p_fee
        self.slippage_usd += (self.qty * self.entry_spot_px * s_slip
                              + self.qty * self.entry_perp_px * p_slip)

    def accrue_funding(self, perp_px: float, rate: float) -> None:
        """记一期资金费（做空永续：rate>0 收取）。"""
        self.funding_usd += self.qty * float(perp_px) * float(rate)
        self.funding_periods += 1

    def close(self, spot_px: float, perp_px: float, ts: float) -> None:
        s_fee = self.spot_taker_bp / 1e4
        p_fee = self.perp_taker_bp / 1e4
        s_slip = self.spot_slippage_bp / 1e4
        p_slip = self.perp_slippage_bp / 1e4
        self.spot_fee_usd += self.qty * spot_px * s_fee
        self.perp_fee_usd += self.qty * perp_px * p_fee
        self.slippage_usd += (self.qty * spot_px * s_slip
                              + self.qty * perp_px * p_slip)
        self.exit_spot_px = float(spot_px)
        self.exit_perp_px = float(perp_px)
        self.exit_ts = float(ts)
        self.closed = True

    # ── 收益分解 ──
    @property
    def spot_pnl_usd(self) -> float:
        return self.qty * (self.exit_spot_px - self.entry_spot_px)

    @property
    def perp_pnl_usd(self) -> float:
        """空头：价格下跌赚钱。"""
        return self.qty * (self.entry_perp_px - self.exit_perp_px)

    @property
    def basis_entry_bp(self) -> float:
        if self.entry_spot_px <= 0:
            return 0.0
        return (self.entry_perp_px - self.entry_spot_px) / self.entry_spot_px * 1e4

    @property
    def basis_exit_bp(self) -> float:
        if self.exit_spot_px <= 0:
            return 0.0
        return (self.exit_perp_px - self.exit_spot_px) / self.exit_spot_px * 1e4

    @property
    def net_usd(self) -> float:
        return (self.spot_pnl_usd + self.perp_pnl_usd + self.funding_usd
                - self.spot_fee_usd - self.perp_fee_usd - self.slippage_usd)

    @property
    def net_bp(self) -> float:
        return self.net_usd / self.notional_usd * 1e4 if self.notional_usd > 0 else 0.0

    @property
    def hold_hours(self) -> float:
        return max(0.0, (self.exit_ts - self.entry_ts) / 3600.0) if self.closed else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "venue": self.venue,
            "notional_usd": round(self.notional_usd, 2),
            "entry_ts": self.entry_ts, "exit_ts": self.exit_ts,
            "hold_hours": round(self.hold_hours, 2),
            "entry_spot_px": self.entry_spot_px, "entry_perp_px": self.entry_perp_px,
            "exit_spot_px": self.exit_spot_px, "exit_perp_px": self.exit_perp_px,
            "basis_entry_bp": round(self.basis_entry_bp, 4),
            "basis_exit_bp": round(self.basis_exit_bp, 4),
            "funding_periods": self.funding_periods,
            "funding_usd": round(self.funding_usd, 6),
            "spot_pnl_usd": round(self.spot_pnl_usd, 6),
            "perp_pnl_usd": round(self.perp_pnl_usd, 6),
            "spot_fee_usd": round(self.spot_fee_usd, 6),
            "perp_fee_usd": round(self.perp_fee_usd, 6),
            "slippage_usd": round(self.slippage_usd, 6),
            "net_usd": round(self.net_usd, 6),
            "net_bp": round(self.net_bp, 4),
        }


def simulate_position(
    *,
    symbol: str,
    venue: str,
    spot_px0: float,
    perp_px0: float,
    spot_px1: float,
    perp_px1: float,
    ts0: float,
    ts1: float,
    notional_usd: float = 1000.0,
    funding_events: Optional[List[Tuple[float, float]]] = None,
    spot_taker_bp: float = DEFAULT_SPOT_TAKER_BP,
    perp_taker_bp: float = DEFAULT_PERP_TAKER_BP,
    spot_slippage_bp: float = DEFAULT_SPOT_SLIPPAGE_BP,
    perp_slippage_bp: float = DEFAULT_PERP_SLIPPAGE_BP,
) -> HedgedCarrySim:
    """模拟一笔对冲 carry（现货多 + 永续空）。

    `funding_events` = [(perp_px, rate), ...] 按结算边界给出。
    """
    if spot_px0 <= 0 or perp_px0 <= 0:
        raise ValueError("entry price must be positive")
    qty = float(notional_usd) / float(spot_px0)
    sim = HedgedCarrySim(
        symbol=symbol, venue=venue, notional_usd=float(notional_usd), qty=qty,
        entry_spot_px=float(spot_px0), entry_perp_px=float(perp_px0),
        entry_ts=float(ts0), spot_taker_bp=spot_taker_bp,
        perp_taker_bp=perp_taker_bp, spot_slippage_bp=spot_slippage_bp,
        perp_slippage_bp=perp_slippage_bp,
    )
    sim.enter()
    for perp_px, rate in (funding_events or []):
        sim.accrue_funding(perp_px, rate)
    sim.close(spot_px1, perp_px1, ts1)
    return sim
