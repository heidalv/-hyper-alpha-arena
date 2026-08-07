# -*- coding: utf-8 -*-
"""
全市场 OI / 资金费率聚合采集器。

聚合各交易所 OI → 全市场 OI 分布；各所费率对比 → 费率套利空间。
各所独立标 available（某所超时=该所 null，不造假）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.services.market_aggregation.aggregate_collector_base import (
    AggregateCollectorBase,
    _create_ccxt_public,
    _ccxt_symbol,
)

logger = logging.getLogger(__name__)


class AggregateMarketCollector(AggregateCollectorBase):
    """全市场 OI 分布 + 资金费率对比 + 套利空间。"""

    VENUES = ["asterdex", "binance", "okx", "hyperliquid"]
    SOURCE_NAME = "AggregateMarket"
    CACHE_TTL = 60  # OI/费率变化慢，60 秒缓存
    MAX_WORKERS = 5

    def _fetch_one_venue(self, venue: str, symbols: List[str]) -> Optional[Dict[str, Any]]:
        """从单个交易所取多币种 OI + 资金费率。"""
        ex = _create_ccxt_public(venue)
        if ex is None:
            return None
        data: Dict[str, Any] = {}
        for sym in symbols:
            try:
                ccxt_sym = _ccxt_symbol(venue, sym)
                ticker = ex.fetch_ticker(ccxt_sym)
                # OI（部分交易所通过 ticker 暴露）
                oi = None
                if hasattr(ex, "fetch_open_interest"):
                    try:
                        oi_info = ex.fetch_open_interest(ccxt_sym)
                        oi = oi_info.get("openInterestAmount") or oi_info.get("openInterest")
                    except Exception:
                        pass
                # 资金费率
                funding = None
                if hasattr(ex, "fetch_funding_rate"):
                    try:
                        fr_info = ex.fetch_funding_rate(ccxt_sym)
                        funding = fr_info.get("fundingRate")
                    except Exception:
                        pass
                data[sym] = {
                    "price": ticker.get("last"),
                    "open_interest": float(oi) if oi else None,
                    "funding_rate": float(funding) if funding else None,
                    "timestamp": time.time(),
                }
            except Exception as e:
                logger.debug(f"[AggregateMarket] {venue} {sym} 失败: {e}")
                data[sym] = None
        if not getattr(ex, "_shared_exchange", False):
            try:
                ex.close()
            except Exception:
                pass
        return data

    def _aggregate(self, venue_results: Dict[str, Any], symbols: List[str]) -> Dict[str, Any]:
        """聚合各所 OI/费率 → 全市场指标。"""
        now = time.time()
        merged: Dict[str, Any] = {}
        for sym in symbols:
            per_venue: Dict[str, Any] = {}
            total_oi = 0.0
            funding_rates: Dict[str, Any] = {}
            prices: List[float] = []

            for venue, venue_data in venue_results.items():
                item = (venue_data or {}).get(sym)
                if not item:
                    per_venue[venue] = {"available": False}
                    funding_rates[venue] = None
                    continue
                per_venue[venue] = {
                    "available": True,
                    "open_interest": item["open_interest"],
                    "funding_rate": item["funding_rate"],
                    "price": item["price"],
                    "source": venue,
                }
                funding_rates[venue] = item["funding_rate"]
                if item["open_interest"]:
                    total_oi += item["open_interest"]
                if item["price"]:
                    prices.append(item["price"])

            # 费率套利空间（最高费率 - 最低费率）
            valid_fundings = [f for f in funding_rates.values() if f is not None]
            funding_arb = None
            if len(valid_fundings) >= 2:
                funding_arb = round(max(valid_fundings) - min(valid_fundings), 8)

            active_count = sum(1 for v in per_venue.values() if v.get("available"))
            merged[sym] = {
                "venues": per_venue,
                "total_oi": round(total_oi, 2) if total_oi > 0 else None,
                "oi_by_exchange": {
                    v: d.get("open_interest")
                    for v, d in per_venue.items() if d.get("available")
                },
                "funding_rates": funding_rates,
                "funding_arbitrage": funding_arb,
                "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
                "active_venues": active_count,
                "total_venues": len(self.VENUES),
                "available": active_count > 0,
                "fetched_at": now,
            }
        return merged

    def _persist(self, result: Dict[str, Any], symbols: List[str]) -> None:
        """归一化：OI/费率写入数据中心仓库 market_asset_metrics。"""
        from backend.services.market_aggregation.persist import persist_market
        persist_market(result, symbols)


# 单例
aggregate_market_collector = AggregateMarketCollector()
