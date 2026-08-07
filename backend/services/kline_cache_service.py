"""
K线统一缓存服务 - 替代碎片化的多层缓存

设计原则:
1. 单一缓存入口，所有 K 线数据查询先查缓存
2. 级联失效：短周期更新 → 自动清理对应长周期缓存
3. TTL 分级：价格快速过期 / 指标中等 / 历史数据长保留
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 缓存 TTL 配置 (秒) ──
PRICE_TTL = 5
KLINE_LATEST_TTL = 30   # 最新 K 线
KLINE_HISTORY_TTL = 300   # 历史 K 线（不常变）
INDICATOR_TTL = 60
PATTERN_TTL = 120
RESONANCE_TTL = 300
SR_TTL = 300

# ── 级联关系：短周期更新时需要清理的长周期缓存 ──
CASCADE_MAP: Dict[str, List[str]] = {
    "1m": ["1m"],
    "3m": ["3m"],
    "5m": ["5m"],
    "15m": ["5m", "15m"],
    "1h":  ["5m", "15m", "1h"],
    "4h":  ["5m", "15m", "1h", "4h"],
    "1d":  ["5m", "15m", "1h", "4h", "1d"],
}


class KlineCacheService:
    """统一 K 线缓存，线程安全"""

    def __init__(self):
        self._lock = threading.Lock()

        # 价格缓存： key = exchange:symbol → (price, ts)
        self._price: Dict[str, Tuple[float, float]] = {}

        # K 线缓存： key = "exchange:symbol:period" → (klines, ts)
        self._klines: Dict[str, Tuple[List[Dict], float]] = {}

        # 指标缓存： key = "exchange:symbol:period" → (indicators, ts)
        self._indicators: Dict[str, Tuple[Dict, float]] = {}

        # 形态缓存： key = "symbol:period" → (patterns, ts)
        self._patterns: Dict[str, Tuple[List[Dict], float]] = {}

        # 共振缓存： key = symbol → (resonance, ts)
        self._resonance: Dict[str, Tuple[Dict, float]] = {}

        # 支撑阻力缓存： key = "symbol:period:method" → (sr_data, ts)
        self._sr: Dict[str, Tuple[Dict, float]] = {}

    # ─── 价格缓存 ───

    def _exchange_key(self, exchange: Optional[str] = None) -> str:
        return (exchange or "default").strip().lower()

    def _price_key(self, symbol: str, exchange: Optional[str] = None) -> str:
        return f"{self._exchange_key(exchange)}:{symbol.upper()}"

    def get_price(self, symbol: str, exchange: Optional[str] = None) -> Optional[float]:
        return self._get(self._price, self._price_key(symbol, exchange), PRICE_TTL)

    def set_price(self, symbol: str, price: float, exchange: Optional[str] = None):
        self._price[self._price_key(symbol, exchange)] = (price, time.time())

    # ─── K 线缓存 ───

    def _kline_key(self, symbol: str, period: str, exchange: Optional[str] = None) -> str:
        return f"{self._exchange_key(exchange)}:{symbol.upper()}:{period}"

    def get_klines(self, symbol: str, period: str, exchange: Optional[str] = None) -> Optional[List[Dict]]:
        return self._get(self._klines, self._kline_key(symbol, period, exchange), KLINE_LATEST_TTL)

    def set_klines(self, symbol: str, period: str, klines: List[Dict], exchange: Optional[str] = None):
        self._klines[self._kline_key(symbol, period, exchange)] = (klines, time.time())

    def invalidate_klines(self, symbol: str, period: str, exchange: Optional[str] = None):
        """使指定 symbol/period 的 K 线缓存失效"""
        with self._lock:
            if exchange:
                self._klines.pop(self._kline_key(symbol, period, exchange), None)
                return
            suffix = f":{symbol.upper()}:{period}"
            for key in list(self._klines.keys()):
                if key.endswith(suffix):
                    self._klines.pop(key, None)

    def invalidate_cascade(self, symbol: str, period: str, exchange: Optional[str] = None):
        """级联失效：短周期 K 线更新后，清理关联的长周期缓存"""
        affected = CASCADE_MAP.get(period, [period])
        with self._lock:
            for p in affected:
                if exchange:
                    key = self._kline_key(symbol, p, exchange)
                    self._klines.pop(key, None)
                    self._indicators.pop(key, None)
                else:
                    suffix = f":{symbol.upper()}:{p}"
                    for key in list(self._klines.keys()):
                        if key.endswith(suffix):
                            self._klines.pop(key, None)
                    for key in list(self._indicators.keys()):
                        if key.endswith(suffix):
                            self._indicators.pop(key, None)
                self._patterns.pop(f"{symbol.upper()}:{p}", None)

    # ─── 指标缓存 ───

    def get_indicators(self, symbol: str, period: str, exchange: Optional[str] = None) -> Optional[Dict]:
        return self._get(self._indicators, self._kline_key(symbol, period, exchange), INDICATOR_TTL)

    def set_indicators(self, symbol: str, period: str, indicators: Dict, exchange: Optional[str] = None):
        self._indicators[self._kline_key(symbol, period, exchange)] = (indicators, time.time())

    # ─── 形态缓存 ───

    def get_patterns(self, symbol: str, period: str) -> Optional[List[Dict]]:
        return self._get(self._patterns, self._kline_key(symbol, period), PATTERN_TTL)

    def set_patterns(self, symbol: str, period: str, patterns: List[Dict]):
        self._patterns[self._kline_key(symbol, period)] = (patterns, time.time())

    # ─── 共振缓存 ───

    def get_resonance(self, symbol: str) -> Optional[Dict]:
        return self._get(self._resonance, symbol.upper(), RESONANCE_TTL)

    def set_resonance(self, symbol: str, resonance: Dict):
        self._resonance[symbol.upper()] = (resonance, time.time())

    # ─── 支撑阻力缓存 ───

    def get_sr(self, symbol: str, period: str, method: str = "pivot") -> Optional[Dict]:
        key = f"{symbol.upper()}:{period}:{method}"
        return self._get(self._sr, key, SR_TTL)

    def set_sr(self, symbol: str, period: str, method: str, sr_data: Dict):
        key = f"{symbol.upper()}:{period}:{method}"
        self._sr[key] = (sr_data, time.time())

    # ─── 工具方法 ───

    def _get(self, store: Dict, key: str, ttl: float) -> Any:
        """TTL 感知的缓存读取"""
        with self._lock:
            entry = store.get(key)
            if entry is None:
                return None
            value, ts = entry
            if time.time() - ts > ttl:
                store.pop(key, None)
                return None
            return value

    def clear_all(self):
        """清除全部缓存"""
        with self._lock:
            self._price.clear()
            self._klines.clear()
            self._indicators.clear()
            self._patterns.clear()
            self._resonance.clear()
            self._sr.clear()
        logger.info("[KlineCache] 全部缓存已清除")

    def stats(self) -> Dict[str, int]:
        """返回缓存统计"""
        with self._lock:
            return {
                "price_entries": len(self._price),
                "klines_entries": len(self._klines),
                "indicator_entries": len(self._indicators),
                "pattern_entries": len(self._patterns),
                "resonance_entries": len(self._resonance),
                "sr_entries": len(self._sr),
            }


# 全局单例
kline_cache = KlineCacheService()
