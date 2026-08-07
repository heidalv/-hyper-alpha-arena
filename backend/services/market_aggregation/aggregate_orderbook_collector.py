# -*- coding: utf-8 -*-
"""
多所聚合订单簿采集器 — 合并 Binance/Bybit/OKX 盘口。

输出全市场买卖失衡 + 跨所价差，各所独立标 available（某所超时=该所 null，不造假）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from backend.services.market_aggregation.aggregate_collector_base import (
    AggregateCollectorBase,
    _create_ccxt_public,
    _ccxt_symbol,
)

logger = logging.getLogger(__name__)


class AggregateOrderbookCollector(AggregateCollectorBase):
    """合并多所订单簿 → 全市场买卖失衡 + 跨所价差。"""

    VENUES = ["asterdex", "binance", "okx", "hyperliquid"]
    SOURCE_NAME = "AggregateOrderbook"
    CACHE_TTL = 5  # 盘口变化快，5 秒缓存
    MAX_WORKERS = 5

    def _fetch_one_venue(self, venue: str, symbols: List[str]) -> Optional[Dict[str, Any]]:
        """从单个交易所取多币种盘口。失败返回 None。"""
        ex = _create_ccxt_public(venue)
        if ex is None:
            return None
        books: Dict[str, Any] = {}
        for sym in symbols:
            try:
                ccxt_sym = _ccxt_symbol(venue, sym)
                ob = ex.fetch_order_book(ccxt_sym, limit=20)
                bids = ob.get("bids", [])
                asks = ob.get("asks", [])
                books[sym] = {
                    "bids": bids[:20],
                    "asks": asks[:20],
                    "best_bid": bids[0][0] if bids else None,
                    "best_ask": asks[0][0] if asks else None,
                    "bid_volume": sum(b[1] for b in bids[:20]),
                    "ask_volume": sum(a[1] for a in asks[:20]),
                    "timestamp": time.time(),
                }
            except Exception as e:
                logger.debug(f"[AggregateOrderbook] {venue} {sym} 失败: {e}")
                books[sym] = None  # 该币在该所取不到
        if hasattr(ex, "close") and not getattr(ex, "_shared_exchange", False):
            try:
                ex.close()
            except Exception:
                pass
        return books

    def _aggregate(self, venue_results: Dict[str, Any], symbols: List[str]) -> Dict[str, Any]:
        """合并多所盘口 → 全市场指标。各所独立标 available。"""
        now = time.time()
        merged: Dict[str, Any] = {}
        for sym in symbols:
            per_venue: Dict[str, Any] = {}
            all_bid_vol = 0.0
            all_ask_vol = 0.0
            best_bids: List[Tuple[float, str]] = []
            best_asks: List[Tuple[float, str]] = []

            for venue, books in venue_results.items():
                book = (books or {}).get(sym)
                if not book or book.get("best_bid") is None:
                    per_venue[venue] = {"available": False}
                    continue
                per_venue[venue] = {
                    "available": True,
                    "best_bid": book["best_bid"],
                    "best_ask": book["best_ask"],
                    "bid_volume": book["bid_volume"],
                    "ask_volume": book["ask_volume"],
                    "source": venue,
                }
                all_bid_vol += book["bid_volume"]
                all_ask_vol += book["ask_volume"]
                best_bids.append((book["best_bid"], venue))
                best_asks.append((book["best_ask"], venue))

            total_vol = all_bid_vol + all_ask_vol
            imbalance = (all_bid_vol - all_ask_vol) / total_vol if total_vol > 0 else None

            # 跨所价差（各所 best bid/ask 的极差）
            spread_bid = (max(b for b, _ in best_bids) - min(b for b, _ in best_bids)) if len(best_bids) >= 2 else None
            spread_ask = (max(a for a, _ in best_asks) - min(a for a, _ in best_asks)) if len(best_asks) >= 2 else None

            active_count = sum(1 for v in per_venue.values() if v.get("available"))
            merged[sym] = {
                "venues": per_venue,
                "merged_bid_volume": round(all_bid_vol, 2),
                "merged_ask_volume": round(all_ask_vol, 2),
                "global_imbalance": round(imbalance, 4) if imbalance is not None else None,
                "best_bid_global": max(b for b, _ in best_bids) if best_bids else None,
                "best_ask_global": min(a for a, _ in best_asks) if best_asks else None,
                "cross_venue_bid_spread": round(spread_bid, 4) if spread_bid is not None else None,
                "cross_venue_ask_spread": round(spread_ask, 4) if spread_ask is not None else None,
                "active_venues": active_count,
                "total_venues": len(self.VENUES),
                "available": active_count > 0,
                "fetched_at": now,
            }
        return merged

    def _persist(self, result: Dict[str, Any], symbols: List[str]) -> None:
        """归一化：盘口快照写入数据中心仓库 market_orderbook_snapshots。"""
        from backend.services.market_aggregation.persist import persist_orderbook
        persist_orderbook(result, symbols)


# 单例
aggregate_orderbook_collector = AggregateOrderbookCollector()
