"""
Paper 套利市价解析 — 多源真实行情（Hub L2 / K 线 / ccxt），供成交与 MTM 共用。
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Paper 模式下部分 DEX/小所无独立 WS，用主流 CEX 盘口作代理（与实盘价差在滑点模型中体现）
PAPER_PRICE_PROXY: Dict[str, str] = {
    "asterdex": "binance",
    "gateio": "binance",
    "okx": "binance",
    "bybit": "binance",
}


@dataclass
class PaperMarketQuote:
    """单 symbol 行情快照"""

    symbol: str
    exchange: str
    mid: float
    bid: float
    ask: float
    mark: float
    spread_bps: float
    funding_rate: float
    source: str
    price_exchange: str
    ts: float
    volume_24h: float = 0.0
    bids: List[List[float]] = field(default_factory=list)
    asks: List[List[float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_symbol(symbol: str) -> Tuple[str, str, List[str]]:
    raw = (symbol or "").strip()
    base = raw.split("/")[0].split("-")[0].upper() if raw else ""
    pair = f"{base}/USDT"
    candidates: List[str] = []
    for sym in (raw, pair, base, f"{base}-USDT"):
        if sym and sym not in candidates:
            candidates.append(sym)
    return base, pair, candidates


def _synthetic_spread(mid: float, bps: float = 2.0) -> Tuple[float, float]:
    half = mid * (bps / 10000.0) / 2.0
    return mid - half, mid + half


def _fetch_ccxt_ticker(exchange_id: str, base: str) -> Optional[PaperMarketQuote]:
    try:
        import ccxt

        opts: Dict[str, Any] = {"enableRateLimit": True, "timeout": 10000}
        if exchange_id == "binance":
            opts["options"] = {"defaultType": "future"}
        ex = getattr(ccxt, exchange_id)(opts)
        for sym in (f"{base}/USDT:USDT", f"{base}/USDT"):
            try:
                ticker = ex.fetch_ticker(sym)
            except Exception:
                continue
            last = float(ticker.get("last") or ticker.get("close") or 0)
            bid = float(ticker.get("bid") or last or 0)
            ask = float(ticker.get("ask") or last or 0)
            if last <= 0 and bid > 0 and ask > 0:
                last = (bid + ask) / 2.0
            if last <= 0:
                continue
            if bid <= 0 or ask <= 0:
                bid, ask = _synthetic_spread(last)
            spread_bps = ((ask - bid) / last * 10000.0) if last > 0 else 0.0
            return PaperMarketQuote(
                symbol=f"{base}/USDT",
                exchange=exchange_id,
                mid=last,
                bid=bid,
                ask=ask,
                mark=last,
                spread_bps=round(spread_bps, 2),
                funding_rate=float(ticker.get("info", {}).get("lastFundingRate", 0) or 0),
                source=f"ccxt:{exchange_id}",
                price_exchange=exchange_id,
                ts=time.time(),
            )
    except Exception as exc:
        logger.debug("[RebatePaperMarket] ccxt %s %s: %s", exchange_id, base, exc)
    return None


def _from_kline_db(base: str, pair: str, price_exchange: str) -> Optional[PaperMarketQuote]:
    try:
        from backend.services.kline_data_service import kline_service

        for sym in (base, pair.replace("/", ""), pair):
            for ex_name in (price_exchange, "binance", "hyperliquid"):
                rows = kline_service.get_klines_from_db(sym, "1m", 1, exchange=ex_name)
                if not rows:
                    continue
                close = float(rows[-1].get("close") or 0)
                if close <= 0:
                    continue
                bid, ask = _synthetic_spread(close, bps=3.0)
                return PaperMarketQuote(
                    symbol=pair,
                    exchange=ex_name,
                    mid=close,
                    bid=bid,
                    ask=ask,
                    mark=close,
                    spread_bps=3.0,
                    funding_rate=0.0,
                    source=f"kline_db:{ex_name}",
                    price_exchange=ex_name,
                    ts=time.time(),
                )
    except Exception as exc:
        logger.debug("[RebatePaperMarket] kline fallback: %s", exc)
    return None


def resolve_paper_market(symbol: str, exchange: str = "") -> Optional[PaperMarketQuote]:
    """
    解析 Paper 用真实行情。优先 Hub L2（bid/ask），其次 K 线 DB，最后 ccxt REST。
    """
    base, pair, candidates = _normalize_symbol(symbol)
    if not base:
        return None

    ex = (exchange or "binance").lower().strip()
    price_ex = PAPER_PRICE_PROXY.get(ex, ex)

    # 1) MarketDataHub — 含 funding / mark / L2
    try:
        from backend.services.market_data_hub import market_data_hub

        for sym in candidates:
            snap = market_data_hub.get_market_snapshot(sym, price_ex)
            l2 = market_data_hub.get_l2(price_ex, sym.upper())
            mid = float(snap.get("mark_price") or snap.get("price") or 0)
            if l2 and l2.mid > 0:
                mid = l2.mid
            if mid <= 0:
                continue
            bid = float(snap.get("bid") or (l2.bid if l2 else 0))
            ask = float(snap.get("ask") or (l2.ask if l2 else 0))
            if bid <= 0 or ask <= 0:
                bid, ask = _synthetic_spread(mid, bps=2.0)
            spread_bps = ((ask - bid) / mid * 10000.0) if mid > 0 else 0.0
            return PaperMarketQuote(
                symbol=sym if "/" in sym else pair,
                exchange=ex,
                mid=mid,
                bid=bid,
                ask=ask,
                mark=float(snap.get("mark_price") or mid),
                spread_bps=round(spread_bps, 2),
                funding_rate=float(snap.get("funding_rate") or 0),
                source="market_data_hub",
                price_exchange=price_ex,
                ts=time.time(),
                volume_24h=float(snap.get("volume_24h") or 0),
                bids=list(l2.bids) if l2 and l2.bids else [],
                asks=list(l2.asks) if l2 and l2.asks else [],
            )
    except Exception as exc:
        logger.debug("[RebatePaperMarket] hub: %s", exc)

    # 2) price_cache / market_price_service
    try:
        from backend.services.market_price_service import get_market_snapshot

        snap = get_market_snapshot(pair, price_ex)
        mid = float(snap.get("mark_price") or snap.get("price") or 0)
        if mid > 0:
            bid = float(snap.get("bid") or 0)
            ask = float(snap.get("ask") or 0)
            if bid <= 0 or ask <= 0:
                bid, ask = _synthetic_spread(mid)
            spread_bps = ((ask - bid) / mid * 10000.0) if mid > 0 else 0.0
            return PaperMarketQuote(
                symbol=pair,
                exchange=ex,
                mid=mid,
                bid=bid,
                ask=ask,
                mark=mid,
                spread_bps=round(spread_bps, 2),
                funding_rate=float(snap.get("funding_rate") or 0),
                source=str(snap.get("source") or "market_price_service"),
                price_exchange=price_ex,
                ts=time.time(),
            )
    except Exception:
        pass

    # 3) K 线 DB
    q = _from_kline_db(base, pair, price_ex)
    if q:
        q.exchange = ex
        return q

    # 4) ccxt REST
    # [2026-08-15 P0-4 修复] DC_ONLY 唯一数据源模式下禁止直连交易所：
    # 上面三层（hub / price_cache / K线DB）都失败时直接返回 None，不再 ccxt 兜底。
    try:
        from backend.services.market_data import _dc_only_enabled
        if _dc_only_enabled():
            logger.warning(
                "[RebatePaperMarket] DC_ONLY 下数据中心无 %s@%s 行情（禁止 ccxt 直连）",
                symbol, exchange,
            )
            return None
    except Exception:
        pass
    for ex_id in ("binance", "hyperliquid"):
        q = _fetch_ccxt_ticker(ex_id, base)
        if q:
            q.exchange = ex
            if ex != ex_id:
                q.source = f"{q.source}_proxy_for_{ex}"
            return q

    logger.warning("[RebatePaperMarket] 无法解析行情 %s@%s", symbol, exchange)
    return None


def walk_orderbook_fill(
    side: str,
    size_usd: float,
    bids: List[List[float]],
    asks: List[List[float]],
    fallback_price: float,
) -> Tuple[float, float]:
    """
    按 L2 深度计算加权成交价。返回 (avg_price, filled_qty)。
    深度不足时在最后一档上加冲击滑点。
    """
    side_l = (side or "buy").lower()
    levels = asks if side_l == "buy" else bids
    if not levels or size_usd <= 0 or fallback_price <= 0:
        return fallback_price, size_usd / fallback_price

    remaining_usd = float(size_usd)
    total_usd = 0.0
    total_qty = 0.0

    for level in levels:
        if len(level) < 2:
            continue
        px, qty = float(level[0]), float(level[1])
        if px <= 0 or qty <= 0:
            continue
        level_usd = px * qty
        take_usd = min(remaining_usd, level_usd)
        take_qty = take_usd / px
        total_usd += take_usd
        total_qty += take_qty
        remaining_usd -= take_usd
        if remaining_usd <= 1e-6:
            break

    if total_qty <= 0:
        return fallback_price, size_usd / fallback_price

    avg_price = total_usd / total_qty
    if remaining_usd > 1e-6:
        try:
            from backend.services.rebate_arb.rebate_paper_simulator import _calc_slippage

            impact = _calc_slippage(size_usd, "intraday", is_close=False)
            if side_l == "buy":
                avg_price *= (1 + impact)
            else:
                avg_price *= (1 - impact)
            total_qty = size_usd / avg_price
        except Exception:
            pass

    return avg_price, total_qty


def pick_reference_price(
    quote: PaperMarketQuote,
    side: str,
    order_type: str,
) -> Tuple[float, str]:
    """Taker 用 ask/bid；Maker limit 用 mid（挂单成交）。"""
    side_l = (side or "buy").lower()
    is_maker = (order_type or "market").lower() == "limit"
    if is_maker:
        return quote.mid, "mid"
    if side_l == "buy":
        return quote.ask if quote.ask > 0 else quote.mid, "ask"
    return quote.bid if quote.bid > 0 else quote.mid, "bid"


def calc_funding_pnl(
    side: str,
    notional_usd: float,
    funding_rate: float,
    hold_hours: float,
) -> float:
    """永续合约资金费累计（每 8h 结算一次）。"""
    if notional_usd <= 0 or hold_hours <= 0 or abs(funding_rate) < 1e-12:
        return 0.0
    periods = hold_hours / 8.0
    # 正 funding：多头支付给空头
    if (side or "buy").lower() == "buy":
        return -notional_usd * funding_rate * periods
    return notional_usd * funding_rate * periods
