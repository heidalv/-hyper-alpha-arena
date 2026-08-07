# -*- coding: utf-8 -*-
"""
全市场实盘数据中台 API — /api/market-intel/*

响应铁律：数据拿不到就是拿不到，value=null + available=false，绝不造假。
每项数据带 {value, source, available, reason, fetched_at} 真实性标记。

端点：
- GET /overview?symbols=BTC,ETH     多维聚合总览
- GET /orderbook/{symbol}?depth=20  多所聚合盘口
- GET /data-health                  数据健康面板
- GET /sources-config               数据源配置读取
- PUT /sources-config               数据源配置保存
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/market-intel", tags=["market-intelligence"])

# 后台预热：首次请求时触发采集（不阻塞当前响应，下次请求就有缓存了）
_warmup_started = False

def _trigger_snapshot_warmup(symbols: List[str]):
    """用 UnifiedDataPool 的独立线程池触发快照采集（不占 asyncio 线程池）。"""
    global _warmup_started
    if _warmup_started:
        return
    _warmup_started = True
    import threading
    def _warm():
        try:
            from backend.services.market_aggregation.aggregate_orderbook_collector import (
                aggregate_orderbook_collector,
            )
            from backend.services.market_aggregation.aggregate_market_collector import (
                aggregate_market_collector,
            )
            aggregate_orderbook_collector.collect(symbols)
            aggregate_market_collector.collect(symbols)
            logger.info("[MarketIntel] 后台预热完成，缓存已填充")
        except Exception as e:
            logger.debug(f"[MarketIntel] 预热失败: {e}")
        finally:
            global _warmup_started
            _warmup_started = False
    threading.Thread(target=_warm, daemon=True, name="market-intel-warmup").start()


def _truthy(value: Any, source: str, available: bool, reason: str = None) -> Dict[str, Any]:
    """构造标准真实性响应项。"""
    return {
        "value": value,
        "source": source,
        "available": available,
        "reason": reason,
        "fetched_at": time.time(),
    }


def _read_dc_snapshot(symbols: List[str]) -> Dict[str, Any]:
    """从运行中的数据中台（K线仓库）读取各所最新价与新鲜度。

    中台改造：全市场数据中台与 data_center（crypto_klines 仓库）打通，
    采集器内存缓存缺失时用此兜底，并如实标注 age_sec/source。
    """
    try:
        from backend.services.data_center import data_center
    except Exception:
        return {sym: {"available": False, "venues": {}} for sym in symbols}

    result: Dict[str, Any] = {}
    for sym in symbols:
        venues: Dict[str, Any] = {}
        best_price: Optional[float] = None
        best_age: Optional[float] = None
        available = False
        for ex in ("asterdex", "binance", "hyperliquid"):
            try:
                kr = data_center.get_klines(
                    sym, "1m", count=1, exchange=ex, purpose="research"
                )
                if kr and kr.rows:
                    row = kr.rows[-1]
                    price = float(row.get("close") or 0)
                    ts = int(row.get("timestamp") or 0)
                    if ts > 1e12:
                        ts = int(ts / 1000)
                    age = max(0.0, time.time() - ts)
                    ok = price > 0 and age <= 600
                    venues[ex] = {
                        "price": price,
                        "age_sec": round(age, 1),
                        "available": ok,
                        "ts": ts,
                    }
                    if ok and (best_age is None or age < best_age):
                        best_price = price
                        best_age = age
                        available = True
            except Exception:
                continue
        result[sym] = {
            "available": available,
            "last_price": best_price,
            "age_sec": round(best_age, 1) if best_age is not None else None,
            "venues": venues,
            "source": "data_center",
        }
    return result


@router.get("/overview")
async def get_overview(symbols: str = Query("BTC,ETH,SOL")):
    """多维聚合总览：每币的盘口 + OI/费率 + 衍生品 + 鲸鱼。每项带真实性标记。"""
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        sym_list = ["BTC"]

    # 纯内存读取（绝不触发 ccxt 同步调用，避免阻塞 asyncio 线程池拖死后端）。
    # 聚合数据由 UnifiedDataPool 后台定时采集（16线程独立池）填充缓存。
    # 缓存空=该项标缺失，前端显示红色"缺失"（绝不造假、绝不阻塞）。
    orderbook_data = _read_orderbook_cache(sym_list)
    market_data = _read_market_cache(sym_list)
    derivatives_data = _collect_derivatives_cached(sym_list)
    whale_data = _read_whale_cache(sym_list)
    # 中台改造：数据中心快照（K线仓库最新价）作为独立真实来源随响应返回
    dc_data = _read_dc_snapshot(sym_list)

    # 对缓存缺失的 symbol 异步触发采集（不阻塞当前响应；下次轮询即有数据）
    missing_syms = [
        sym for sym in sym_list
        if not orderbook_data.get(sym, {}).get("available")
        or not market_data.get(sym, {}).get("available")
        or not whale_data.get(sym, {}).get("available")
    ]
    if missing_syms:
        _trigger_snapshot_warmup(missing_syms)
        _trigger_whale_warmup(missing_syms)

    result: Dict[str, Any] = {}
    for sym in sym_list:
        ob = orderbook_data.get(sym, {})
        mk = market_data.get(sym, {})
        der = derivatives_data.get(sym, {})
        wh = whale_data.get(sym, {})
        dc = dc_data.get(sym, {"available": False, "venues": {}})

        result[sym] = {
            "dc": dc,
            "orderbook": {
                "available": ob.get("available", False),
                "active_venues": ob.get("active_venues", 0),
                "total_venues": ob.get("total_venues", 0),
                "global_imbalance": ob.get("global_imbalance"),
                "best_bid": ob.get("best_bid_global"),
                "best_ask": ob.get("best_ask_global"),
                "cross_venue_spread": ob.get("cross_venue_bid_spread"),
                "venues": ob.get("venues", {}),
            },
            "market": {
                "available": mk.get("available", False),
                "total_oi": mk.get("total_oi"),
                "funding_rates": mk.get("funding_rates", {}),
                "funding_arbitrage": mk.get("funding_arbitrage"),
                "oi_by_exchange": mk.get("oi_by_exchange", {}),
                "venues": mk.get("venues", {}),
            },
            "derivatives": {
                "available": der.get("available", False) if der else False,
                "funding_rate": der.get("funding_rate"),
                "signal": der.get("signal"),
                "signal_strength": der.get("signal_strength"),
                "liquidation_long": der.get("liquidation_1h_long"),
                "liquidation_short": der.get("liquidation_1h_short"),
                "long_short_ratio": der.get("long_short_ratio"),
                "data_sources": der.get("data_sources", ""),
            },
            "whale": {
                # 有真实鲸鱼数据才标 available（total_usd>0 表示有实际异动）
                "available": bool(wh) and (wh.get("total_usd", 0) or 0) > 0,
                "direction": wh.get("direction") if wh else None,
                "total_usd": wh.get("total_usd") if wh else None,
                "confidence": wh.get("confidence") if wh else None,
                "whale_count": wh.get("whale_count") if wh else None,
                "net_usd": wh.get("net_usd") if wh else None,
                "active_venues": wh.get("active_venues") if wh else 0,
                "venues": wh.get("venues") if wh else {},
            },
        }
    return {"symbols": result, "fetched_at": time.time()}


@router.get("/watchlist")
async def get_watchlist():
    """聚合交易对监控列表：用户配置 + AI运行中 + 自动选币，去重+标注来源。

    三个来源合并（绝不用默认列表兜底，没有就是没有）：
    - user:  用户在配置页设置的常用交易对 (system_configs.user_trading_pairs)
    - active: 运行中会话手动选币 (FullAutoSession.symbols, status=running/defensive/paused)
    - auto:  自动选币注入的币种 (FullAutoSession.auto_coin_symbols)
    """
    from backend.services.trading_pairs_config import get_user_trading_pairs

    # 来源1: 用户配置的常用交易对（自带60s缓存，db=None自动开关session）
    try:
        user_pairs = [s.upper() for s in get_user_trading_pairs()]
    except Exception as e:
        logger.debug(f"[MarketIntel] get_user_trading_pairs 失败: {e}")
        user_pairs = []

    # 来源2+3: 运行中会话的手动选币 + 自动选币（查DB，内存JSON列）
    active_pairs: set = set()
    auto_pairs: set = set()
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession

        db = SessionLocal()
        try:
            sessions = db.query(FullAutoSession).filter(
                FullAutoSession.status.in_(["running", "defensive", "paused"])
            ).all()
            for s in sessions:
                for sym in (s.symbols or []):
                    active_pairs.add(str(sym).upper())
                for sym in (s.auto_coin_symbols or []):
                    auto_pairs.add(str(sym).upper())
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[MarketIntel] 会话交易对查询失败: {e}")

    # 合并去重 + 标注每个 symbol 的来源
    all_symbols = sorted(set(user_pairs) | active_pairs | auto_pairs)
    details = []
    for sym in all_symbols:
        sources = []
        if sym in user_pairs:
            sources.append("user")
        if sym in active_pairs:
            sources.append("active")
        if sym in auto_pairs:
            sources.append("auto")
        details.append({"symbol": sym, "sources": sources})

    return {
        "symbols": all_symbols,
        "details": details,
        "counts": {
            "user": len(set(user_pairs)),
            "active": len(active_pairs),
            "auto": len(auto_pairs),
            "total": len(all_symbols),
        },
        "fetched_at": time.time(),
    }


def _read_orderbook_cache(symbols: List[str]) -> Dict[str, Any]:
    """中台归一化：优先读数据中心仓库 market_orderbook_snapshots，内存缓存兜底。"""
    result: Dict[str, Any] = {}
    try:
        from sqlalchemy import text as _sa_text
        from backend.database.connection import MarketSessionLocal

        now = time.time()
        with MarketSessionLocal() as db:
            rows = db.execute(
                _sa_text(
                    """
                    SELECT symbol, exchange, best_bid, best_ask, bid_depth_5, ask_depth_5, timestamp
                    FROM market_orderbook_snapshots
                    WHERE symbol = ANY(:syms) AND timestamp >= :cut
                    ORDER BY timestamp DESC
                    """
                ),
                {"syms": symbols, "cut": int(now) - 300},
            ).fetchall()
        by_sym: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            venues = by_sym.setdefault(r.symbol, {})
            if r.exchange in venues:
                continue  # 同批同所只取最新一行
            venues[r.exchange] = {
                "available": True,
                "best_bid": float(r.best_bid) if r.best_bid is not None else None,
                "best_ask": float(r.best_ask) if r.best_ask is not None else None,
                "bid_volume": float(r.bid_depth_5 or 0),
                "ask_volume": float(r.ask_depth_5 or 0),
                "source": "data_center",
            }
        for sym in symbols:
            venues = by_sym.get(sym, {})
            if not venues:
                continue
            all_bid = sum(v["bid_volume"] for v in venues.values())
            all_ask = sum(v["ask_volume"] for v in venues.values())
            total_vol = all_bid + all_ask
            bids = [v["best_bid"] for v in venues.values() if v["best_bid"] is not None]
            asks = [v["best_ask"] for v in venues.values() if v["best_ask"] is not None]
            result[sym] = {
                "venues": venues,
                "global_imbalance": round((all_bid - all_ask) / total_vol, 4) if total_vol > 0 else None,
                "best_bid_global": max(bids) if bids else None,
                "best_ask_global": min(asks) if asks else None,
                "cross_venue_bid_spread": round(max(bids) - min(bids), 4) if len(bids) >= 2 else None,
                "cross_venue_ask_spread": round(max(asks) - min(asks), 4) if len(asks) >= 2 else None,
                "active_venues": len(venues),
                "total_venues": len(venues),
                "available": True,
                "fetched_at": now,
            }
    except Exception:
        pass

    # 兜底：采集器内存缓存（缺失 symbol）
    missing = [s for s in symbols if s not in result]
    if missing:
        try:
            from backend.services.market_aggregation.aggregate_orderbook_collector import (
                aggregate_orderbook_collector,
            )
            for sym in missing:
                found = False
                for ck, cv in aggregate_orderbook_collector._cache.items():
                    if sym in cv:
                        result[sym] = cv[sym]
                        found = True
                        break
                if not found:
                    result[sym] = {"available": False, "reason": "no_data"}
        except Exception:
            for sym in missing:
                result[sym] = {"available": False, "reason": "no_data"}
    return result


def _read_market_cache(symbols: List[str]) -> Dict[str, Any]:
    """中台归一化：优先读数据中心仓库 market_asset_metrics + perp_funding，内存缓存兜底。"""
    result: Dict[str, Any] = {}
    try:
        from sqlalchemy import text as _sa_text
        from backend.database.connection import MarketSessionLocal

        now = time.time()
        with MarketSessionLocal() as db:
            metric_rows = db.execute(
                _sa_text(
                    """
                    SELECT symbol, exchange, open_interest, funding_rate, mark_price, timestamp
                    FROM market_asset_metrics
                    WHERE symbol = ANY(:syms) AND timestamp >= :cut
                    ORDER BY timestamp DESC
                    """
                ),
                {"syms": symbols, "cut": int(now) - 600},
            ).fetchall()
            funding_rows = db.execute(
                _sa_text(
                    """
                    SELECT DISTINCT ON (symbol, exchange) symbol, exchange, funding_rate, mark_price, timestamp
                    FROM perp_funding
                    WHERE symbol = ANY(:syms) AND timestamp >= :cut_ms
                    ORDER BY symbol, exchange, timestamp DESC
                    """
                ),
                {"syms": symbols, "cut_ms": int(now * 1000) - 3600 * 1000},
            ).fetchall()

        by_sym: Dict[str, Dict[str, Any]] = {}
        for r in metric_rows:
            venues = by_sym.setdefault(r.symbol, {})
            if r.exchange in venues:
                continue
            venues[r.exchange] = {
                "available": True,
                "open_interest": float(r.open_interest) if r.open_interest is not None else None,
                "funding_rate": float(r.funding_rate) if r.funding_rate is not None else None,
                "price": float(r.mark_price) if r.mark_price is not None else None,
                "source": "data_center",
            }
        for r in funding_rows:
            venues = by_sym.setdefault(r.symbol, {})
            if r.exchange in venues and venues[r.exchange].get("funding_rate") is not None:
                continue
            venues[r.exchange] = {
                "available": True,
                "open_interest": None,
                "funding_rate": float(r.funding_rate) if r.funding_rate is not None else None,
                "price": float(r.mark_price) if r.mark_price is not None else None,
                "source": "data_center",
            }

        for sym in symbols:
            venues = by_sym.get(sym, {})
            if not venues:
                continue
            total_oi = sum(v["open_interest"] for v in venues.values() if v.get("open_interest"))
            funding_rates = {v: d.get("funding_rate") for v, d in venues.items()}
            valid_fundings = [f for f in funding_rates.values() if f is not None]
            prices = [v["price"] for v in venues.values() if v.get("price")]
            result[sym] = {
                "venues": venues,
                "total_oi": round(total_oi, 2) if total_oi > 0 else None,
                "oi_by_exchange": {
                    v: d.get("open_interest") for v, d in venues.items() if d.get("available")
                },
                "funding_rates": funding_rates,
                "funding_arbitrage": round(max(valid_fundings) - min(valid_fundings), 8)
                if len(valid_fundings) >= 2 else None,
                "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
                "active_venues": len(venues),
                "total_venues": len(venues),
                "available": True,
                "fetched_at": now,
            }
    except Exception:
        pass

    missing = [s for s in symbols if s not in result]
    if missing:
        try:
            from backend.services.market_aggregation.aggregate_market_collector import (
                aggregate_market_collector,
            )
            for sym in missing:
                found = False
                for ck, cv in aggregate_market_collector._cache.items():
                    if sym in cv:
                        result[sym] = cv[sym]
                        found = True
                        break
                if not found:
                    result[sym] = {"available": False, "reason": "no_data"}
        except Exception:
            for sym in missing:
                result[sym] = {"available": False, "reason": "no_data"}
    return result


def _read_whale_cache(symbols: List[str]) -> Dict[str, Any]:
    """中台归一化：优先读数据中心仓库 whale_activities，内存缓存兜底。"""
    result: Dict[str, Any] = {}
    try:
        from sqlalchemy import text as _sa_text
        from backend.database.connection import MarketSessionLocal

        with MarketSessionLocal() as db:
            rows = db.execute(
                _sa_text(
                    """
                    SELECT symbol, from_entity, amount_usd, signal_direction, count(*) AS cnt
                    FROM whale_activities
                    WHERE activity_type='aggregate_whale'
                      AND symbol = ANY(:syms)
                      AND timestamp >= now() - interval '30 minutes'
                    GROUP BY symbol, from_entity, amount_usd, signal_direction
                    """
                ),
                {"syms": symbols},
            ).fetchall()
        by_sym: Dict[str, Any] = {}
        for r in rows:
            item = by_sym.setdefault(r.symbol, {"buy": 0.0, "sell": 0.0, "count": 0, "venues": {}})
            amt = float(r.amount_usd or 0)
            sd = float(r.signal_direction or 0)
            if sd >= 0:
                item["buy"] += amt
            else:
                item["sell"] += amt
            item["count"] += int(r.cnt or 1)
            item["venues"][r.from_entity or "?"] = {
                "available": True,
                "whale_buy_usd": amt if sd >= 0 else 0,
                "whale_sell_usd": amt if sd < 0 else 0,
                "count": int(r.cnt or 1),
                "source": "data_center",
            }
        for sym, item in by_sym.items():
            total_usd = item["buy"] + item["sell"]
            net = item["buy"] - item["sell"]
            result[sym] = {
                "venues": item["venues"],
                "direction": round(net / total_usd, 4) if total_usd > 0 else None,
                "total_usd": round(total_usd, 2) if total_usd > 0 else None,
                "net_usd": round(net, 2),
                "whale_count": item["count"],
                "confidence": round(min(1.0, item["count"] / 5.0), 2),
                "active_venues": len(item["venues"]),
                "total_venues": len(item["venues"]),
                "available": total_usd > 0,
            }
    except Exception:
        pass

    missing = [s for s in symbols if s not in result]
    if missing:
        try:
            from backend.services.market_aggregation.aggregate_whale_collector import (
                aggregate_whale_collector,
            )
            for sym in missing:
                found = False
                for ck, cv in aggregate_whale_collector._cache.items():
                    if sym in cv:
                        result[sym] = cv[sym]
                        found = True
                        break
                if not found:
                    result[sym] = {"available": False, "reason": "no_data"}
        except Exception:
            for sym in missing:
                result[sym] = {"available": False, "reason": "no_data"}
    return result


_whale_warmup_started = False

def _trigger_whale_warmup(symbols: List[str]):
    """后台触发鲸鱼采集（不阻塞当前响应；下次轮询即有数据）。"""
    global _whale_warmup_started
    if _whale_warmup_started:
        return
    _whale_warmup_started = True
    import threading
    def _warm():
        try:
            from backend.services.market_aggregation.aggregate_whale_collector import (
                aggregate_whale_collector,
            )
            aggregate_whale_collector.collect(symbols)
            logger.info(f"[MarketIntel] 鲸鱼采集预热完成: {symbols}")
        except Exception as e:
            logger.debug(f"[MarketIntel] 鲸鱼预热失败: {e}")
        finally:
            global _whale_warmup_started
            _whale_warmup_started = False
    threading.Thread(target=_warm, daemon=True, name="market-intel-whale-warmup").start()


def _collect_orderbook(symbols: List[str]) -> Dict[str, Any]:
    try:
        from backend.services.market_aggregation.aggregate_orderbook_collector import (
            aggregate_orderbook_collector,
        )
        return aggregate_orderbook_collector.collect(symbols)
    except Exception as e:
        logger.debug(f"[MarketIntel] orderbook 采集失败: {e}")
        return {}


def _collect_aggregate_market(symbols: List[str]) -> Dict[str, Any]:
    try:
        from backend.services.market_aggregation.aggregate_market_collector import (
            aggregate_market_collector,
        )
        return aggregate_market_collector.collect(symbols)
    except Exception as e:
        logger.debug(f"[MarketIntel] aggregate_market 采集失败: {e}")
        return {}


def _collect_derivatives(symbols: List[str]) -> Dict[str, Any]:
    try:
        from backend.services.derivatives_analytics_service import derivatives_analytics
        result = {}
        for sym in symbols:
            snap = derivatives_analytics.get_snapshot(sym)
            if snap:
                result[sym] = {
                    "available": True,
                    "funding_rate": snap.funding_rate,
                    "signal": snap.signal,
                    "signal_strength": snap.signal_strength,
                    "liquidation_1h_long": snap.liquidation_1h_long,
                    "liquidation_1h_short": snap.liquidation_1h_short,
                    "long_short_ratio": snap.long_short_ratio,
                    "data_sources": snap.data_sources,
                }
            else:
                result[sym] = {"available": False}
        return result
    except Exception as e:
        logger.debug(f"[MarketIntel] derivatives 采集失败: {e}")
        return {}


def _collect_whale(symbols: List[str]) -> Dict[str, Any]:
    try:
        from backend.services.whale_tracker_service import whale_tracker
        result = {}
        for sym in symbols:
            sig = whale_tracker.get_whale_signal(sym)
            if sig:
                result[sym] = {
                    "direction": getattr(sig, "direction", 0),
                    "total_usd": getattr(sig, "total_usd", 0),
                    "confidence": getattr(sig, "confidence", 0),
                }
        return result
    except Exception as e:
        logger.debug(f"[MarketIntel] whale 采集失败: {e}")
        return {}


def _collect_derivatives_cached(symbols: List[str]) -> Dict[str, Any]:
    """读衍生品缓存快照（不实时调外部API，避免阻塞事件循环）。"""
    try:
        from backend.services.derivatives_analytics_service import derivatives_analytics
        result = {}
        for sym in symbols:
            snap = derivatives_analytics.get_cached_snapshot(sym)
            if snap:
                result[sym] = {
                    "available": True,
                    "funding_rate": snap.funding_rate,
                    "signal": snap.signal,
                    "signal_strength": snap.signal_strength,
                    "liquidation_1h_long": snap.liquidation_1h_long,
                    "liquidation_1h_short": snap.liquidation_1h_short,
                    "long_short_ratio": snap.long_short_ratio,
                    "data_sources": snap.data_sources,
                }
            else:
                result[sym] = {"available": False}
        return result
    except Exception as e:
        logger.debug(f"[MarketIntel] derivatives 缓存读取失败: {e}")
        return {}


def _collect_whale_cached(symbols: List[str]) -> Dict[str, Any]:
    """读鲸鱼缓存信号（不实时调链上API）。"""
    try:
        from backend.services.whale_tracker_service import whale_tracker
        result = {}
        for sym in symbols:
            try:
                sig = whale_tracker.get_whale_signal(sym)
                if sig:
                    result[sym] = {
                        "direction": getattr(sig, "direction", 0),
                        "total_usd": getattr(sig, "total_usd", 0),
                        "confidence": getattr(sig, "confidence", 0),
                    }
            except Exception:
                pass
        return result
    except Exception:
        return {}


@router.get("/orderbook/{symbol}")
async def get_orderbook(symbol: str, depth: int = Query(20, ge=1, le=50)):
    """多所聚合盘口。各所独立标 available，失败=该所 null。"""
    # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止 collect() 直连，
    # 从数据中心仓库读回。
    try:
        from backend.services.market_data import _dc_only_enabled
        if _dc_only_enabled():
            from backend.services.market_aggregation.aggregate_orderbook_collector import (
                aggregate_orderbook_collector,
            )
            db_result = _read_orderbook_cache([symbol.upper()])
            if db_result.get(symbol.upper(), {}).get("available"):
                return db_result[symbol.upper()]
            # DB 无数据 → 返回空（绝不直连）
            return {"available": False, "reason": "dc_only_db_empty"}
    except Exception:
        pass
    data = await asyncio.to_thread(_collect_orderbook, [symbol.upper()])
    return data.get(symbol.upper(), {"available": False, "reason": "no_data"})


@router.get("/data-health")
async def get_data_health():
    """数据健康面板：各数据源在线状态 + 各币数据完整度。"""
    health: Dict[str, Any] = {}
    from datetime import datetime

    # 聚合采集器 venue 健康
    try:
        from backend.services.market_aggregation.aggregate_orderbook_collector import (
            aggregate_orderbook_collector,
        )
        from backend.services.market_aggregation.aggregate_market_collector import (
            aggregate_market_collector,
        )
        health["orderbook_venues"] = aggregate_orderbook_collector.get_venue_health()
        health["market_venues"] = aggregate_market_collector.get_venue_health()
    except Exception as e:
        health["venue_error"] = str(e)[:100]

    # ── 中台改造：数据中心（K线仓库）健康 + BTC 就绪度（读 data_center，不再读空缓存）──
    dc_online = False
    try:
        from backend.services.data_center import data_center

        heartbeats = data_center.get_sync_heartbeats() or []
        # asterdex p0 心跳 5 分钟内成功 = 数据中心在线
        for hb in heartbeats:
            if hb.get("exchange") == "asterdex" and hb.get("pool") == "p0":
                ls = hb.get("last_success_at")
                if ls:
                    try:
                        dt = datetime.fromisoformat(str(ls))
                        if (datetime.now() - dt).total_seconds() < 300:
                            dc_online = True
                    except Exception:
                        pass

        dc_snap = _read_dc_snapshot(["BTC"]).get("BTC", {})
        k1h = data_center.get_klines("BTC", "1h", count=2, exchange="asterdex", purpose="research")
        k4h = data_center.get_klines("BTC", "4h", count=2, exchange="asterdex", purpose="research")
        price_ok = bool(dc_snap.get("available"))
        klines_ok = bool(k1h.rows) and bool(k4h.rows)
        indicators_ok = bool(k1h and k1h.count >= 50)
        derivatives_ok = False
        try:
            from backend.database.connection import MarketSessionLocal
            from sqlalchemy import text as _sa_text
            with MarketSessionLocal() as _mdb:
                _ts = _mdb.execute(
                    _sa_text(
                        "SELECT max(timestamp) FROM perp_funding "
                        "WHERE symbol='BTC' AND exchange='hyperliquid'"
                    )
                ).scalar()
            if _ts:
                _tsi = int(_ts)
                if _tsi > 1e12:
                    _tsi = int(_tsi / 1000)
                derivatives_ok = (time.time() - _tsi) < 3600
        except Exception:
            pass
        missing = []
        warnings = []
        if not price_ok:
            missing.append("price")
        if not klines_ok:
            missing += ["kline_1h", "kline_4h"]
        if not indicators_ok:
            missing.append("indicators")
        if not derivatives_ok:
            missing.append("derivatives")
        if k1h.rows and k1h.stale_sec is not None and k1h.stale_sec > 3600:
            warnings.append("kline_1h_stale")
        if k4h.rows and k4h.stale_sec is not None and k4h.stale_sec > 4 * 3600:
            warnings.append("kline_4h_stale")

        health["btc_readiness"] = {
            "price_ok": price_ok,
            "klines_ok": klines_ok,
            "indicators_ok": indicators_ok,
            "derivatives_ok": derivatives_ok,
            "missing": missing,
            "warnings": warnings,
            "source": "data_center",
        }
        health["data_center"] = {
            "online": dc_online,
            "heartbeats": heartbeats,
            "btc_dc": dc_snap,
        }
    except Exception as e:
        health["data_center_error"] = str(e)[:200]

    # 计算总可用率（采集器 venue + 数据中心在线）
    all_checks = []
    for vk in ("orderbook_venues", "market_venues"):
        for v, info in (health.get(vk) or {}).items():
            all_checks.append(info.get("healthy", False))
    all_checks.append(dc_online)
    health["overall_score"] = round(sum(1 for c in all_checks if c) / max(len(all_checks), 1), 2)
    health["fetched_at"] = time.time()
    return health


@router.get("/sources-config")
async def get_sources_config():
    """读取数据源配置（各所开关 + Key状态 + 健康）。"""
    import os
    config: Dict[str, Any] = {"venues": {}, "aggregate_sources": {}}

    # 交易所开关（基于环境变量）
    venue_list = [
        ("asterdex", "Asterdex", "ASTERDEX_API_KEY"),
        ("binance", "Binance", "BINANCE_API_KEY"),
        ("okx", "OKX", "OKX_API_KEY"),
        ("hyperliquid", "Hyperliquid", "HYPERLIQUID_API_KEY"),
    ]
    for vid, name, key_env in venue_list:
        config["venues"][vid] = {
            "name": name,
            "api_key_configured": bool(os.environ.get(key_env)),
            "public_api": vid != "hyperliquid",  # 非hyperliquid可用公共API
        }

    # 聚合数据源
    agg_sources = [
        ("coinglass", "CoinGlass", "COINGLASS_API_KEY"),
        ("coinalyze", "Coinalyze", "COINALYZE_API_KEY"),
        ("whale_alert", "Whale Alert", "WHALE_ALERT_API_KEY"),
        ("glassnode", "Glassnode(付费)", "GLASSNODE_API_KEY"),
        ("cryptopanic", "CryptoPanic", "CRYPTOPANIC_API_KEY"),
    ]
    for sid, name, key_env in agg_sources:
        config["aggregate_sources"][sid] = {
            "name": name,
            "api_key_configured": bool(os.environ.get(key_env)),
        }

    # 采集器健康
    try:
        from backend.services.market_aggregation.aggregate_orderbook_collector import (
            aggregate_orderbook_collector,
        )
        config["venue_health"] = aggregate_orderbook_collector.get_venue_health()
    except Exception:
        pass

    config["fetched_at"] = time.time()
    return config


@router.put("/sources-config")
async def update_sources_config(config: Dict[str, Any]):
    """保存数据源配置（当前只记录，不立即改 .env，需重启生效）。"""
    logger.info(f"[MarketIntel] 数据源配置更新请求: {list(config.keys())}")
    return {"success": True, "message": "配置已记录，部分需重启后端生效", "fetched_at": time.time()}
