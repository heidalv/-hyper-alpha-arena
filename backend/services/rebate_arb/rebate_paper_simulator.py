"""
Rebate 套利 Paper 成交模拟 — 滑点、手续费、返佣

与 AI 模拟盘 (paper_trading_engine) 和 fee_guard 保持同一套成本模型。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 各交易所默认费率（incentive 缓存不可用时降级）
EXCHANGE_FEE_DEFAULTS: Dict[str, Dict[str, float]] = {
    # Stage 6 官方费率：USDT 永续 0% maker / 0.04% taker，无返佣
    "asterdex": {"maker": 0.0, "taker": 0.0004, "rebate_rate": 0.0},
    "hyperliquid": {"maker": 0.0002, "taker": 0.00035, "rebate_rate": 0.0},
    "binance": {"maker": 0.0002, "taker": 0.0004, "rebate_rate": 0.10},
    "okx": {"maker": 0.0002, "taker": 0.0005, "rebate_rate": 0.0},
    "bybit": {"maker": 0.0002, "taker": 0.00055, "rebate_rate": 0.0},
    "gateio": {"maker": 0.0002, "taker": 0.0005, "rebate_rate": 0.0},
}


@dataclass
class PaperLegFill:
    """单腿 Paper 成交明细"""

    exchange: str
    side: str
    order_type: str
    size_usd: float
    ref_price: float
    filled_price: float
    size_coins: float
    slippage_rate: float
    slippage_cost_usd: float
    fee_rate: float
    fee_paid: float
    rebate_rate: float
    rebate_received: float
    is_maker: bool
    is_close: bool = False
    price_source: str = ""
    quote_exchange: str = ""
    ref_price_kind: str = ""
    spread_bps: float = 0.0
    funding_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_exchange_fee_profile(exchange: str) -> Dict[str, float]:
    """读取交易所费率；优先 incentive 缓存，否则用默认值。"""
    ex = (exchange or "").lower().strip()
    defaults = EXCHANGE_FEE_DEFAULTS.get(ex, EXCHANGE_FEE_DEFAULTS["hyperliquid"])

    try:
        from backend.services.rebate_arb.incentive_aggregator import incentive_aggregator

        latest = incentive_aggregator.get_latest()
        summary = latest.get(ex)
        if summary and summary.fee_tier:
            tier = summary.fee_tier
            return {
                "maker": float(tier.maker_rate or defaults["maker"]),
                "taker": float(tier.taker_rate or defaults["taker"]),
                "rebate_rate": float(tier.rebate_rate or defaults["rebate_rate"]),
            }
    except Exception as exc:
        logger.debug("[RebatePaperSim] incentive 费率读取失败 %s: %s", ex, exc)

    return dict(defaults)


def _calc_slippage(
    notional_usd: float,
    trade_nature: str = "intraday",
    is_close: bool = False,
) -> float:
    try:
        from backend.services.fee_guard import calc_slippage_rate

        # 平仓通常按 taker 市价，冲击略大
        rate = calc_slippage_rate(notional_usd, trade_nature, is_sl=is_close)
        if is_close:
            rate = min(rate * 1.15, 0.003)
        return rate
    except Exception:
        return 0.0005


def simulate_leg_fill(
    *,
    exchange: str,
    side: str,
    order_type: str,
    size_usd: float,
    ref_price: float = 0.0,
    trade_nature: str = "intraday",
    is_close: bool = False,
    market: Optional[Any] = None,
    symbol: str = "",
) -> Optional[PaperLegFill]:
    """
    模拟单腿成交：真实 bid/ask + 订单簿深度 + 手续费 + 返佣。

    优先使用 market (PaperMarketQuote)；否则降级 ref_price + 滑点模型。
    """
    from .rebate_paper_market import (
        PaperMarketQuote,
        pick_reference_price,
        resolve_paper_market,
        walk_orderbook_fill,
    )
    from backend.services.exchange.paper_exchange_simulator import simulate_notional_order

    quote: Optional[PaperMarketQuote] = market
    if quote is None and symbol:
        quote = resolve_paper_market(symbol, exchange)

    if quote and quote.mid > 0:
        ref_px, ref_kind = pick_reference_price(quote, side, order_type)
        is_maker = (order_type or "market").lower() == "limit"
        side_l = (side or "buy").lower()

        if is_maker:
            filled_price = ref_px
            size_coins = size_usd / filled_price
        elif quote.bids or quote.asks:
            filled_price, size_coins = walk_orderbook_fill(
                side_l,
                size_usd,
                quote.bids,
                quote.asks,
                ref_px,
            )
        else:
            sim = simulate_notional_order(
                exchange=exchange,
                symbol=symbol,
                side=side_l,
                order_type=order_type or "market",
                target_notional_usd=size_usd,
                reference_price=ref_px,
                bid=quote.bids[0][0] if quote.bids else ref_px,
                ask=quote.asks[0][0] if quote.asks else ref_px,
            )
            if sim.status.value != "filled":
                return None
            filled_price = sim.fill_price
            size_coins = sim.filled_quantity

        ref_price = quote.mid
        spread_bps = quote.spread_bps
        price_source = quote.source
        quote_exchange = quote.price_exchange
        funding_rate = quote.funding_rate
    else:
        if size_usd <= 0 or ref_price <= 0:
            return None
        is_maker = (order_type or "market").lower() == "limit"
        side_l = (side or "buy").lower()
        if is_maker:
            filled_price = ref_price
            size_coins = size_usd / filled_price
        else:
            slip = _calc_slippage(size_usd, trade_nature, is_close=is_close)
            if is_close:
                slip = min(slip * 1.15, 0.003)
            sim = simulate_notional_order(
                exchange=exchange,
                symbol=symbol,
                side=side_l,
                order_type=order_type or "market",
                target_notional_usd=size_usd,
                reference_price=ref_price,
                bid=ref_price * (1 - slip),
                ask=ref_price * (1 + slip),
            )
            if sim.status.value != "filled":
                return None
            filled_price = sim.fill_price
            size_coins = sim.filled_quantity
        spread_bps = 0.0
        price_source = "ref_price_fallback"
        quote_exchange = exchange
        funding_rate = 0.0
        ref_kind = "mid"

    profile = get_exchange_fee_profile(exchange)
    is_maker = (order_type or "market").lower() == "limit"
    fee_rate = profile["maker"] if is_maker else profile["taker"]
    rebate_rate = float(profile.get("rebate_rate", 0.0))
    actual_notional = filled_price * size_coins

    side_l = (side or "buy").lower()
    fee_paid = actual_notional * fee_rate
    rebate_received = fee_paid * rebate_rate if is_maker else 0.0
    slippage_cost = abs(filled_price - ref_price) * size_coins if ref_price > 0 else 0.0
    slip_rate = abs(filled_price - ref_price) / ref_price if ref_price > 0 else 0.0

    return PaperLegFill(
        exchange=(exchange or "").lower(),
        side=side_l,
        order_type=order_type or "market",
        size_usd=float(actual_notional),
        ref_price=float(ref_price),
        filled_price=float(filled_price),
        size_coins=float(size_coins),
        slippage_rate=float(slip_rate),
        slippage_cost_usd=float(slippage_cost),
        fee_rate=float(fee_rate),
        fee_paid=float(fee_paid),
        rebate_rate=float(rebate_rate),
        rebate_received=float(rebate_received),
        is_maker=is_maker,
        is_close=is_close,
        price_source=price_source,
        quote_exchange=quote_exchange or exchange,
        ref_price_kind=ref_kind if quote and quote.mid > 0 else "mid",
        spread_bps=float(spread_bps),
        funding_rate=float(funding_rate),
    )


def build_order_from_fill(
    leg: Dict[str, Any],
    fill: PaperLegFill,
    *,
    order_id: str,
) -> Dict[str, Any]:
    """将 PaperLegFill 转为 engine 使用的 order 字典。"""
    return {
        "order_id": order_id,
        "exchange": fill.exchange or leg.get("exchange", ""),
        "symbol": leg.get("symbol", ""),
        "side": fill.side,
        "type": fill.order_type,
        "size_usd": fill.size_usd,
        "size": fill.size_coins,
        "status": "filled",
        "filled_price": fill.filled_price,
        "ref_price": fill.ref_price,
        "fee_paid": fill.fee_paid,
        "rebate_received": fill.rebate_received,
        "slippage_rate": fill.slippage_rate,
        "slippage_cost_usd": fill.slippage_cost_usd,
        "fee_rate": fill.fee_rate,
        "paper": True,
        "paper_fill": fill.to_dict(),
    }


def calc_leg_round_trip_pnl(
    entry: PaperLegFill,
    exit_fill: PaperLegFill,
) -> Dict[str, float]:
    """计算单腿开平仓净 PnL（含手续费与返佣）。"""
    qty = entry.size_coins
    if entry.side == "buy":
        gross = (exit_fill.filled_price - entry.filled_price) * qty
    else:
        gross = (entry.filled_price - exit_fill.filled_price) * qty

    total_fee = entry.fee_paid + exit_fill.fee_paid
    total_rebate = entry.rebate_received + exit_fill.rebate_received
    net = gross - total_fee + total_rebate

    return {
        "gross_pnl": gross,
        "total_fee": total_fee,
        "total_rebate": total_rebate,
        "net_pnl": net,
        "slippage_cost": entry.slippage_cost_usd + exit_fill.slippage_cost_usd,
    }


def calc_unrealized_leg_pnl(entry: PaperLegFill, mark_price: float) -> Dict[str, float]:
    """开仓后按标记价估算未实现 PnL（仅扣开仓手续费，不含平仓成本）。"""
    if mark_price <= 0 or entry.size_coins <= 0:
        return {"gross_pnl": 0.0, "net_pnl": 0.0, "mark_price": mark_price}

    qty = entry.size_coins
    if entry.side == "buy":
        gross = (mark_price - entry.filled_price) * qty
    else:
        gross = (entry.filled_price - mark_price) * qty

    net = gross - entry.fee_paid + entry.rebate_received
    return {
        "gross_pnl": gross,
        "net_pnl": net,
        "mark_price": mark_price,
        "entry_price": entry.filled_price,
        "size_coins": qty,
    }


def summarize_fills(fills: list[PaperLegFill]) -> Dict[str, float]:
    """汇总多腿成交成本。"""
    return {
        "fee_paid": sum(f.fee_paid for f in fills),
        "rebate_received": sum(f.rebate_received for f in fills),
        "slippage_cost_usd": sum(f.slippage_cost_usd for f in fills),
    }
