# -*- coding: utf-8 -*-
"""
全市场聚合数据采集基类 — 泛化自 multi_venue_funding_collector 的成功模式。

子类只需实现 _fetch_one_venue + _aggregate，自动获得：
- 并发抓取（ThreadPoolExecutor）
- 健康诊断（连续失败计数）
- 诚实空转（失败 venue 不出现，绝不造假）
- 数据真实性标记（每项带 source/available/reason）

铁律：数据拿不到就是拿不到，绝不用 0/50/1.0 占位。
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 各交易所连续失败计数（健康诊断）
_venue_fail_count: Dict[str, int] = {}
_VENUE_FAIL_THRESHOLD = 5  # 连续失败 N 次后暂时跳过该 venue


def _get_proxy() -> Optional[str]:
    """读取代理配置（ccxt 创建时透传）。"""
    return os.environ.get("BINANCE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY")


def _create_ccxt_public(exchange: str, timeout: int = 10000):
    """创建 ccxt 公共只读客户端（无需 API key），自动配代理。"""
    import ccxt
    if exchange == "asterdex":
        # asterdex 与 Binance 完全兼容：用 binance 驱动 + 覆盖 base URL
        cls = getattr(ccxt, "binance", None)
        if cls is None:
            return None
        config: Dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": timeout,
            "options": {"defaultType": "future"},
        }
        proxy = _get_proxy()
        if proxy:
            config["proxies"] = {"http": proxy, "https": proxy}
        ex = cls(config)
        ex.urls["api"] = {
            "fapiPublic": "https://fapi.asterdex.com/fapi/v1",
            "fapiPrivate": "https://fapi.asterdex.com/fapi/v1",
            "fapiPublicV2": "https://fapi.asterdex.com/fapi/v2",
            "fapiPrivateV2": "https://fapi.asterdex.com/fapi/v2",
            "public": "https://api.asterdex.com/api/v3",
            "private": "https://api.asterdex.com/api/v3",
            "vapiPublic": "https://fapi.asterdex.com/vapi/v1",
        }
        ex.urls["www"] = "https://www.asterdex.com"
        return ex
    if exchange == "binance":
        # 只用永续合约端点（binanceusdm），避免现货 exchangeInfo 超时
        cls = getattr(ccxt, "binanceusdm", None)
        if cls is None:
            return None
        config: Dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": timeout,
        }
        proxy = _get_proxy()
        if proxy:
            config["proxies"] = {"http": proxy, "https": proxy}
        return cls(config)
    if exchange == "hyperliquid":
        # 用项目内已 patch 市场的 HL 客户端（共享实例，禁止 close）
        try:
            from backend.services.hyperliquid_market_data import (
                get_default_hyperliquid_client,
            )
            client = get_default_hyperliquid_client()
            hl_ex = getattr(client, "exchange", None)
            if hl_ex is not None:
                try:
                    setattr(hl_ex, "_shared_exchange", True)
                except Exception:
                    pass
                return hl_ex
        except Exception:
            pass
    cls = getattr(ccxt, exchange, None)
    if cls is None:
        return None
    config: Dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": timeout,
        "options": {"defaultType": "future"},
    }
    proxy = _get_proxy()
    if proxy:
        config["proxies"] = {"http": proxy, "https": proxy}
    return cls(config)


def _ccxt_symbol(venue: str, symbol: str) -> str:
    """统一 ccxt 永续 symbol：hyperliquid 以 USDC 计价，其余以 USDT 计价。"""
    base = (symbol or "").upper().split("-")[0].split("/")[0]
    if venue == "hyperliquid":
        return f"{base}/USDC:USDC"
    return f"{base}/USDT:USDT"


class AggregateCollectorBase:
    """通用聚合采集基类。子类设置 VENUES + SOURCE_NAME，实现 _fetch_one_venue + _aggregate。"""

    VENUES: List[str] = []
    SOURCE_NAME: str = "aggregate"
    CACHE_TTL: int = 30  # 默认 30 秒缓存
    MAX_WORKERS: int = 8

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._cache_ts: float = 0.0

    def collect(self, symbols: List[str]) -> Dict[str, Any]:
        """主入口：并发抓取所有 venue，聚合返回。失败 venue 诚实跳过。"""
        cache_key = ",".join(sorted(symbols))
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - self._cache_ts < self.CACHE_TTL:
            return cached

        # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止采集器直连
        # 交易所（聚合采集属数据中心采集层职责，主服务进程内的按需直连视为旁路）。
        # 直接返回空聚合，由 API 层 _read_*_cache 从数据中心仓库读回。
        try:
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                logger.info(
                    f"[{self.SOURCE_NAME}] DC_ONLY：跳过直连采集，改读数据中心仓库"
                )
                result = {
                    sym: {
                        "venues": {},
                        "available": False,
                        "reason": "dc_only_read_db",
                    }
                    for sym in symbols
                }
                self._cache[cache_key] = result
                self._cache_ts = now
                return result
        except Exception:
            pass

        healthy_venues = [
            v for v in self.VENUES
            if _venue_fail_count.get(v, 0) < _VENUE_FAIL_THRESHOLD
        ]
        if not healthy_venues:
            logger.warning(f"[{self.SOURCE_NAME}] 所有 venue 连续失败，本轮空转")
            result = {sym: {"available": False, "reason": "all_venues_failed"} for sym in symbols}
            return result

        venue_results: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._fetch_one_venue, venue, symbols): venue
                for venue in healthy_venues
            }
            for future in as_completed(futures):
                venue = futures[future]
                try:
                    data = future.result()
                    if data:
                        venue_results[venue] = data
                        _venue_fail_count.pop(venue, None)  # 成功则清零
                except Exception as e:
                    _venue_fail_count[venue] = _venue_fail_count.get(venue, 0) + 1
                    fails = _venue_fail_count[venue]
                    logger.warning(
                        f"[{self.SOURCE_NAME}] {venue} 采集失败({fails}次): {type(e).__name__}: {str(e)[:100]}"
                    )

        result = self._aggregate(venue_results, symbols)
        # 归一化：聚合结果写入数据中心仓库（失败不阻塞采集）
        try:
            self._persist(result, symbols)
        except Exception as _pe:
            logger.warning(f"[{self.SOURCE_NAME}] 落盘失败: {_pe}")
        self._cache[cache_key] = result
        self._cache_ts = now
        return result

    def _fetch_one_venue(self, venue: str, symbols: List[str]) -> Optional[Dict[str, Any]]:
        """子类实现：从单个 venue 抓取。失败返回 None（不造假）。"""
        raise NotImplementedError

    def _aggregate(self, venue_results: Dict[str, Any], symbols: List[str]) -> Dict[str, Any]:
        """子类实现：把多 venue 结果聚合成统一结构。"""
        raise NotImplementedError

    def _persist(self, result: Dict[str, Any], symbols: List[str]) -> None:
        """子类实现：把聚合结果写入数据中心仓库（归一化）。默认 no-op。"""
        return

    def get_venue_health(self) -> Dict[str, Any]:
        """返回各 venue 健康状态（供前端数据源配置页展示）。"""
        return {
            venue: {
                "fail_count": _venue_fail_count.get(venue, 0),
                "healthy": _venue_fail_count.get(venue, 0) < _VENUE_FAIL_THRESHOLD,
            }
            for venue in self.VENUES
        }
