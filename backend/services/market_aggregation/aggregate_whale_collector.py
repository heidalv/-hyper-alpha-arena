# -*- coding: utf-8 -*-
"""
多所聚合鲸鱼/大单采集器 — 从 Binance/Bybit/OKX 逐笔成交检测大单。

用 ccxt fetch_trades 获取最近成交，筛选大单（>$100K），计算每币种：
- 净流入方向：大单买入额 - 大单卖出额（归一化到 -1 ~ +1）
- 异动金额：大单总 USD
- 置信度：大单笔数 / 阈值

覆盖所有币种（不限 BTC），各所独立标 available（某所超时=该所 null，不造假）。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from backend.services.market_aggregation.aggregate_collector_base import (
    AggregateCollectorBase,
    _create_ccxt_public,
)

logger = logging.getLogger(__name__)

# 大单阈值（USD）。低于此值不算鲸鱼行为。
# $50K 对 BTC 是中等大单，对中低市值币种也能捕获到像样的异动。
WHALE_THRESHOLD_USD = 50_000


class AggregateWhaleCollector(AggregateCollectorBase):
    """多所逐笔成交 → 大单检测 → 净资金流方向 + 异动金额。"""

    VENUES = ["binance", "bybit", "okx"]
    SOURCE_NAME = "AggregateWhale"
    CACHE_TTL = 30  # 鲸鱼行为中频变化，30 秒缓存
    MAX_WORKERS = 5

    def _fetch_one_venue(self, venue: str, symbols: List[str]) -> Optional[Dict[str, Any]]:
        """从单个交易所取多币种最近成交，筛选大单。失败返回 None。"""
        ex = _create_ccxt_public(venue)
        if ex is None:
            return None
        venue_data: Dict[str, Any] = {}
        for sym in symbols:
            try:
                ccxt_sym = f"{sym}/USDT:USDT"
                trades = ex.fetch_trades(ccxt_sym, limit=100)
                whale_buys = 0.0
                whale_sells = 0.0
                whale_count = 0
                largest = 0.0
                for t in trades:
                    cost = t.get("cost", 0) or 0
                    if cost < WHALE_THRESHOLD_USD:
                        continue
                    side = t.get("side", "")
                    if side == "buy":
                        whale_buys += cost
                    elif side == "sell":
                        whale_sells += cost
                    whale_count += 1
                    if cost > largest:
                        largest = cost
                venue_data[sym] = {
                    "whale_buy_usd": round(whale_buys, 2),
                    "whale_sell_usd": round(whale_sells, 2),
                    "count": whale_count,
                    "largest_usd": round(largest, 2),
                    "trade_total": len(trades),
                }
            except Exception as e:
                logger.debug(f"[AggregateWhale] {venue} {sym} 失败: {e}")
                venue_data[sym] = None  # 该币在该所取不到
        try:
            ex.close()
        except Exception:
            pass
        return venue_data

    def _aggregate(self, venue_results: Dict[str, Any], symbols: List[str]) -> Dict[str, Any]:
        """合并多所大单 → 每币种净方向 + 异动金额 + 置信度。"""
        now = time.time()
        merged: Dict[str, Any] = {}
        for sym in symbols:
            per_venue: Dict[str, Any] = {}
            total_buy = 0.0
            total_sell = 0.0
            total_count = 0

            for venue, vdata in venue_results.items():
                item = (vdata or {}).get(sym)
                if not item:
                    per_venue[venue] = {"available": False}
                    continue
                buy = item.get("whale_buy_usd", 0) or 0
                sell = item.get("whale_sell_usd", 0) or 0
                cnt = item.get("count", 0) or 0
                # 该所有大单才算 available（避免 fetch_trades 成功但无大单时误导）
                has_whale = cnt > 0
                per_venue[venue] = {
                    "available": has_whale,
                    "whale_buy_usd": buy if has_whale else None,
                    "whale_sell_usd": sell if has_whale else None,
                    "count": cnt,
                    "largest_usd": item.get("largest_usd", 0) if has_whale else None,
                    "source": venue,
                }
                if has_whale:
                    total_buy += buy
                    total_sell += sell
                    total_count += cnt

            total_usd = total_buy + total_sell
            net = total_buy - total_sell
            # 归一化方向到 -1 ~ +1（用总额做分母，避免极值）
            direction = round(net / total_usd, 4) if total_usd > 0 else None
            active_count = sum(1 for v in per_venue.values() if v.get("available"))
            # 置信度：大单笔数越多越可信，5笔满分
            confidence = round(min(1.0, total_count / 5.0), 2) if total_count > 0 else 0.0

            merged[sym] = {
                "venues": per_venue,
                "direction": direction,
                "total_usd": round(total_usd, 2) if total_usd > 0 else None,
                "net_usd": round(net, 2),
                "whale_count": total_count,
                "confidence": confidence,
                "active_venues": active_count,
                "total_venues": len(self.VENUES),
                "available": total_usd > 0,
                "fetched_at": now,
            }
        return merged

    def _persist(self, result: Dict[str, Any], symbols: List[str]) -> None:
        """归一化：鲸鱼/大单写入数据中心仓库 whale_activities。"""
        from backend.services.market_aggregation.persist import persist_whale
        persist_whale(result, symbols)

    def get_recent_trades_summary(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """AutoCoin 专用：从最近一次聚合缓存读取单币鲸鱼摘要（不触发全所 fetch）。

        返回结构兼容 auto_coin_selector._fetch_onchain_data：
          net_direction / buy_volume / sell_volume / net_usd /
          whale_count / confidence / available
        """
        del limit  # 缓存摘要不按笔数切片；保留参数兼容调用方
        sym = (symbol or "").upper()
        empty = {
            "net_direction": "neutral",
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "net_usd": 0.0,
            "whale_count": 0,
            "confidence": 0.0,
            "available": False,
        }
        if not sym:
            return empty

        thr = float(os.getenv("AUTO_COIN_WHALE_DIR_THRESHOLD", "0.15"))
        # 在所有缓存批次里找该币（collect 的 key 是 symbols 排序串）
        hit: Optional[Dict[str, Any]] = None
        for batch in (self._cache or {}).values():
            if not isinstance(batch, dict):
                continue
            item = batch.get(sym) or batch.get(symbol)
            if isinstance(item, dict) and item.get("available"):
                hit = item
                break

        if not hit:
            return empty

        direction = hit.get("direction")
        if direction is None:
            net_dir = "neutral"
        elif float(direction) > thr:
            net_dir = "buy"
        elif float(direction) < -thr:
            net_dir = "sell"
        else:
            net_dir = "neutral"

        net_usd = float(hit.get("net_usd") or 0.0)
        total_usd = float(hit.get("total_usd") or 0.0)
        # 由净额与总额反推买卖额（无明细时的近似）
        buy_volume = max(0.0, (total_usd + net_usd) / 2.0) if total_usd else max(0.0, net_usd)
        sell_volume = max(0.0, (total_usd - net_usd) / 2.0) if total_usd else max(0.0, -net_usd)

        return {
            "net_direction": net_dir,
            "buy_volume": round(buy_volume, 2),
            "sell_volume": round(sell_volume, 2),
            "net_usd": round(net_usd, 2),
            "whale_count": int(hit.get("whale_count") or 0),
            "confidence": float(hit.get("confidence") or 0.0),
            "available": True,
        }


# 单例
aggregate_whale_collector = AggregateWhaleCollector()
