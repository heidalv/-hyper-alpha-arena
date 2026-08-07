"""
MarketScanner — 全市场扫描器

定期扫描所有交易对，基于多维度评分识别高价值交易机会。
与 CandidatePool 配合实现动态交易对管理。

设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第5.1节 + 第5.4节
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════
#  Data Classes
# ════════════════════════════════════════════════════════

@dataclass
class SymbolScore:
    """交易对评分"""
    symbol: str
    total_score: float              # 综合得分 0~100
    volume_score: float             # 成交量得分
    volatility_score: float         # 波动率得分
    trend_score: float              # 趋势强度得分
    funding_score: float            # 资金费率机会得分
    anomaly_score: float            # 异常得分
    reasons: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ScanResult:
    """扫描结果"""
    scan_id: str
    total_symbols_scanned: int
    qualified_symbols: List[SymbolScore]
    new_opportunities: List[str]      # 新发现的机会
    removed_symbols: List[str]        # 不再符合条件的
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class CandidatePool:
    """候选池 — 高价值交易对动态管理"""
    active: Dict[str, SymbolScore] = field(default_factory=dict)
    cooling_down: Dict[str, datetime] = field(default_factory=dict)
    blacklist: Set[str] = field(default_factory=set)
    max_active: int = 20
    cooling_period_hours: int = 24

    def should_add(self, score: SymbolScore) -> bool:
        """入池规则"""
        if score.symbol in self.blacklist:
            return False
        if score.symbol in self.cooling_down:
            cooldown_end = self.cooling_down[score.symbol]
            if datetime.now() < cooldown_end:
                return False
        if score.total_score < 40:
            return False
        if len(self.active) >= self.max_active:
            min_score = min(self.active.values(), key=lambda x: x.total_score)
            if score.total_score <= min_score.total_score:
                return False
        return True

    def should_remove(self, symbol: str) -> bool:
        """出池规则"""
        if symbol not in self.active:
            return False
        score = self.active[symbol]
        return score.total_score < 30

    def update(self, scan_result: ScanResult):
        """根据扫描结果更新候选池"""
        new_active = {s.symbol: s for s in scan_result.qualified_symbols}

        # 移除不再符合条件的
        for sym in scan_result.removed_symbols:
            if sym in self.active:
                del self.active[sym]
                self.cooling_down[sym] = datetime.now()

        # 添加新的
        for sym, score in new_active.items():
            if self.should_add(score):
                self.active[sym] = score

    def clear_expired_cooldowns(self):
        """清理过期的冷却期"""
        now = datetime.now()
        expired = [
            sym for sym, end_time in self.cooling_down.items()
            if now >= end_time
        ]
        for sym in expired:
            del self.cooling_down[sym]


# ════════════════════════════════════════════════════════
#  MarketScanner
# ════════════════════════════════════════════════════════

class MarketScanner:
    """
    全市场扫描器

    定期扫描所有交易对，基于多维度评分识别高价值交易机会。
    评分维度：成交量(0-25)、波动率(0-25)、趋势强度(0-25)、
             资金费率机会(0-15)、异常得分(0-10)
    """

    MIN_24H_VOLUME = 1_000_000       # 最低24小时成交量($1M)
    MIN_VOLATILITY = 0.02            # 最低日波动率2%
    MAX_SPREAD = 0.005               # 最大买卖价差0.5%
    TOP_N = 20                       # 最多保留前N个
    RESCAN_INTERVAL = 3600           # 重扫间隔(秒), 1小时
    MIN_QUALIFY_SCORE = 30           # 最低合格分数

    # 缓存：避免每次扫描都重新拉取全市场交易对
    # 按交易所分别缓存，支持多交易所
    _cached_all_symbols: Dict[str, List[str]] = {}
    _cached_symbols_ts: Dict[str, datetime] = {}
    _SYMBOL_CACHE_TTL = 1800         # 交易对列表缓存30分钟

    def __init__(self, data_pool=None, exchange_client=None):
        self.data_pool = data_pool
        self.client = exchange_client
        self._current_pool: Set[str] = set()
        self._last_scan: Optional[datetime] = None
        self._history: Dict[str, List[float]] = {}
        self._market_data_service = None
        if data_pool is None:
            try:
                from backend.services import market_data as _md
                self._market_data_service = _md
            except Exception:
                pass

    @classmethod
    def get_all_tradable_symbols(cls, exchange: str = "asterdex") -> List[str]:
        """
        动态获取指定交易所全部可交易的交易对列表。

        多交易所支持：
          - hyperliquid: 三级降级策略（meta API → CCXT → DB缓存）
          - binance/bybit/okx/gateio/asterdex: 通过 ExchangeClientFactory + CCXT 获取
        结果带 30 分钟按交易所内存缓存。

        Args:
            exchange: 交易所标识 (hyperliquid/binance/bybit/okx/gateio/asterdex)
        """
        now = datetime.now()
        cache = cls._cached_all_symbols.get(exchange, [])
        cache_ts = cls._cached_symbols_ts.get(exchange)
        if (cache
                and len(cache) >= 10
                and cache_ts
                and (now - cache_ts).total_seconds() < cls._SYMBOL_CACHE_TTL):
            return cache

        if exchange == "hyperliquid":
            symbols = cls._load_hyperliquid_symbols()
        else:
            symbols = cls._load_ccxt_exchange_symbols(exchange)

        if symbols and len(symbols) >= 10:
            from backend.services.symbol_normalizer import is_valid_base_symbol
            symbols = [s for s in symbols if is_valid_base_symbol(s)]
            cls._cached_all_symbols[exchange] = symbols
            cls._cached_symbols_ts[exchange] = now
            logger.info(f"[MarketScanner] Final symbol count for {exchange}: {len(symbols)}")
        else:
            logger.warning(f"[MarketScanner] No symbols available for {exchange}")

        return cls._cached_all_symbols.get(exchange, [])

    @classmethod
    def _load_hyperliquid_symbols(cls) -> List[str]:
        """Hyperliquid 交易对获取（三级降级策略）"""
        symbols: List[str] = []

        # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连 HL meta
        # API，统一从数据中心 symbol_catalog 目录读取。
        try:
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                from backend.services.kline_sync_meta import list_catalog_symbols
                symbols = list_catalog_symbols("hyperliquid")
                if symbols:
                    logger.info(
                        f"[MarketScanner] DC_ONLY: {len(symbols)} symbols from symbol_catalog (hyperliquid)"
                    )
                    return symbols
        except Exception:
            pass

        # 来源 1: 直接调 Hyperliquid meta API（最权威、最快）
        try:
            import requests as _req
            for url in [
                "https://api.hyperliquid.xyz/info",
                "https://api.hyperliquid-testnet.xyz/info",
            ]:
                try:
                    resp = _req.post(url, json={"type": "meta"}, timeout=10)
                    resp.raise_for_status()
                    data = resp.json()
                    universe = data.get("universe") or []
                    if universe:
                        seen = set()
                        skipped_delisted = 0
                        for entry in universe:
                            if not isinstance(entry, dict):
                                continue
                            # 已下架币无 K 线，进 P1 只会拖死批次（如 MATIC）
                            if entry.get("isDelisted") is True:
                                skipped_delisted += 1
                                continue
                            raw = entry.get("name") or entry.get("symbol") or ""
                            sym = str(raw).upper()
                            if sym and sym not in seen:
                                seen.add(sym)
                                symbols.append(sym)
                        if symbols:
                            logger.info(
                                f"[MarketScanner] Loaded {len(symbols)} symbols from Hyperliquid meta API "
                                f"({url}, skipped_delisted={skipped_delisted})"
                            )
                            break
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"[MarketScanner] Hyperliquid meta API failed: {e}")

        # 来源 2: CCXT load_markets()
        if len(symbols) < 10:
            try:
                # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止 CCXT
                # load_markets 直连兜底（symbol_catalog 为空时宁可返回空目录）。
                from backend.services.market_data import _dc_only_enabled
                if _dc_only_enabled():
                    logger.warning(
                        "[MarketScanner] DC_ONLY: hyperliquid symbol_catalog 为空，"
                        "禁止 CCXT load_markets 直连兜底"
                    )
                    return symbols
                from backend.services.hyperliquid_market_data import get_hyperliquid_client_for_environment
                client = get_hyperliquid_client_for_environment("mainnet")
                if client.exchange:
                    markets = client.exchange.load_markets()
                    seen = set(symbols)
                    for key in markets:
                        base = key.split("/")[0].upper()
                        if base and base not in seen:
                            seen.add(base)
                            symbols.append(base)
                    logger.info(f"[MarketScanner] After CCXT merge: {len(symbols)} symbols")
            except Exception as e:
                logger.warning(f"[MarketScanner] CCXT load_markets failed: {e}")

        # 来源 3: DB 缓存（最后手段）
        if len(symbols) < 10:
            try:
                from backend.services.hyperliquid_symbol_service import get_available_symbols
                for entry in get_available_symbols():
                    sym = entry.get("symbol", "").upper()
                    if sym and sym not in set(symbols):
                        symbols.append(sym)
            except Exception:
                pass

        return symbols

    @classmethod
    def _load_ccxt_exchange_symbols(cls, exchange: str) -> List[str]:
        """
        通过同步 HTTP / 同步 CCXT 获取指定交易所的全量交易对。
        支持: binance / bybit / okx / gateio / asterdex

        注意：禁止用 ExchangeClientFactory 里的 async ccxt 实例调 load_markets()，
        会得到 coroutine 导致目录永远为空。
        """
        # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连交易所
        # exchangeInfo 拉目录，统一从数据中心 symbol_catalog 读取。
        try:
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                from backend.services.kline_sync_meta import list_catalog_symbols
                symbols = list_catalog_symbols(exchange)
                if symbols:
                    logger.info(
                        f"[MarketScanner] DC_ONLY: {len(symbols)} symbols from symbol_catalog ({exchange})"
                    )
                    return symbols
                logger.warning(
                    f"[MarketScanner] DC_ONLY: {exchange} symbol_catalog 为空，禁止直连兜底"
                )
                return []
        except Exception:
            pass
        import os as _os
        import requests as _req

        symbols: List[str] = []
        _proxy_url = (
            _os.environ.get("HTTPS_PROXY")
            or _os.environ.get("https_proxy")
            or _os.environ.get("BINANCE_HTTPS_PROXY")
            or _os.environ.get("MARKET_DATA_HTTP_PROXY")
        )
        _proxies = None
        if _proxy_url:
            _proxies = {
                "https": _proxy_url,
                "http": _os.environ.get("HTTP_PROXY")
                or _os.environ.get("http_proxy")
                or _proxy_url,
            }

        def _from_binance_style_exchange_info(url: str, label: str) -> List[str]:
            resp = _req.get(url, timeout=20, proxies=_proxies)
            resp.raise_for_status()
            data = resp.json()
            out: List[str] = []
            seen: Set[str] = set()
            for s in data.get("symbols", []) or []:
                # 只要永续/交易中
                status = (s.get("status") or "").upper()
                if status and status not in ("TRADING", ""):
                    continue
                ct = (s.get("contractType") or s.get("contract_type") or "").upper()
                if ct and ct not in ("PERPETUAL", "SWAP", ""):
                    continue
                base = (s.get("baseAsset") or s.get("base_currency") or "").upper()
                if base and base not in seen:
                    seen.add(base)
                    out.append(base)
            logger.info(f"[MarketScanner] Loaded {len(out)} symbols from {label} via exchangeInfo")
            return out

        # Binance / AsterDEX：直连 futures exchangeInfo（最稳）
        if exchange in ("asterdex", "binance"):
            url = (
                "https://fapi.asterdex.com/fapi/v1/exchangeInfo"
                if exchange == "asterdex"
                else "https://fapi.binance.com/fapi/v1/exchangeInfo"
            )
            try:
                symbols = _from_binance_style_exchange_info(url, exchange)
                if symbols:
                    return symbols
            except Exception as e:
                logger.warning(f"[MarketScanner] {exchange} exchangeInfo failed: {e}")

        # OKX：公有 instruments API（SWAP）
        if exchange == "okx":
            try:
                resp = _req.get(
                    "https://www.okx.com/api/v5/public/instruments",
                    params={"instType": "SWAP"},
                    timeout=20,
                    proxies=_proxies,
                )
                resp.raise_for_status()
                data = resp.json()
                seen: Set[str] = set()
                for s in data.get("data", []) or []:
                    if (s.get("state") or "").lower() not in ("live", ""):
                        continue
                    inst = (s.get("instId") or "").upper()  # BTC-USDT-SWAP
                    base = inst.split("-")[0] if inst else ""
                    if base and base not in seen:
                        seen.add(base)
                        symbols.append(base)
                if symbols:
                    logger.info(f"[MarketScanner] Loaded {len(symbols)} symbols from okx via instruments")
                    return symbols
            except Exception as e:
                logger.warning(f"[MarketScanner] okx instruments failed: {e}")

        # 通用：同步 ccxt（非 async）
        try:
            import ccxt as _ccxt
            factory = {
                "binance": lambda: _ccxt.binanceusdm({"enableRateLimit": True, "timeout": 20000}),
                "okx": lambda: _ccxt.okx({"enableRateLimit": True, "timeout": 20000,
                                          "options": {"defaultType": "swap"}}),
                "bybit": lambda: _ccxt.bybit({"enableRateLimit": True, "timeout": 20000,
                                             "options": {"defaultType": "linear"}}),
                "gateio": lambda: _ccxt.gateio({"enableRateLimit": True, "timeout": 20000,
                                               "options": {"defaultType": "swap"}}),
                "asterdex": lambda: _ccxt.binanceusdm({
                    "enableRateLimit": True, "timeout": 20000,
                    "urls": {"api": {
                        "fapiPublic": "https://fapi.asterdex.com/fapi/v1",
                        "fapiPrivate": "https://fapi.asterdex.com/fapi/v1",
                    }},
                }),
            }.get(exchange)
            if not factory:
                logger.warning(f"[MarketScanner] no sync ccxt factory for {exchange}")
                return symbols
            ex = factory()
            if _proxy_url:
                ex.proxies = _proxies
            markets = ex.load_markets()
            seen2: Set[str] = set()
            for key, m in (markets or {}).items():
                if not isinstance(key, str) or "/" not in key:
                    continue
                # 只要 swap/future
                mtype = (m.get("type") or "").lower() if isinstance(m, dict) else ""
                if mtype and mtype not in ("swap", "future", "perpetual"):
                    continue
                base = key.split("/")[0].upper()
                if base and base not in seen2:
                    seen2.add(base)
                    symbols.append(base)
            logger.info(f"[MarketScanner] Loaded {len(symbols)} symbols from {exchange} via sync ccxt")
        except Exception as e:
            logger.warning(f"[MarketScanner] {exchange} sync ccxt load_markets failed: {e}")

        return symbols

    def _get_klines(self, symbol: str, period: str = '1h', count: int = 100):
        """Get kline data via data_pool or direct API fallback"""
        if self.data_pool is not None:
            return self.data_pool.get_klines(symbol, period)
        # Fallback: use market_data service directly
        if self._market_data_service is not None:
            try:
                raw = self._market_data_service.get_kline_data(symbol, period=period, count=count)
                if raw and len(raw) >= 24:
                    df = pd.DataFrame(raw)
                    # Ensure standard column names
                    col_map = {'open_price': 'open', 'high_price': 'high', 'low_price': 'low', 'close_price': 'close'}
                    for old, new in col_map.items():
                        if old in df.columns and new not in df.columns:
                            df = df.rename(columns={old: new})
                    return df
            except Exception as e:
                logger.warning(f"[MarketScanner] kline fallback failed for {symbol}: {e}")
        return None

    def _get_market_data(self, symbol: str) -> dict:
        """Get market summary data (funding_rate, volume, etc.)"""
        if self.data_pool is not None:
            return self.data_pool.get_market_data(symbol) or {}
        # Fallback: use ticker data
        if self._market_data_service is not None:
            try:
                ticker = self._market_data_service.get_ticker_data(symbol)
                return {
                    'funding_rate': ticker.get('funding_rate', 0),
                    'volume24h': ticker.get('volume24h', 0),
                    'price': ticker.get('price', 0),
                    'open_interest': ticker.get('open_interest', 0),
                }
            except Exception as e:
                logger.warning(f"[MarketScanner] market data fallback failed for {symbol}: {e}")
        return {}

    def should_rescan(self) -> bool:
        """判断是否需要重新扫描"""
        if self._last_scan is None:
            return True
        elapsed = (datetime.now() - self._last_scan).total_seconds()
        return elapsed >= self.RESCAN_INTERVAL

    async def full_scan(self, all_symbols: List[str]) -> ScanResult:
        """
        执行全市场扫描

        Args:
            all_symbols: 从 exchange_client.get_all_symbols() 获取
        """
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        loop = asyncio.get_event_loop()

        def _eval_sync(sym: str) -> Optional[SymbolScore]:
            try:
                return self._evaluate_symbol_sync(sym)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=min(10, len(all_symbols))) as pool:
            tasks = [loop.run_in_executor(pool, _eval_sync, sym) for sym in all_symbols]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        scores = []
        for r in results:
            if isinstance(r, SymbolScore) and r.total_score > self.MIN_QUALIFY_SCORE:
                scores.append(r)

        # 按得分排序取前N
        scores.sort(key=lambda x: x.total_score, reverse=True)
        qualified = scores[:self.TOP_N]

        # 计算新增和移除
        new_pool = {s.symbol for s in qualified}
        new_opps = list(new_pool - self._current_pool)
        removed = list(self._current_pool - new_pool)
        self._current_pool = new_pool

        # 更新历史
        for s in qualified:
            self._history.setdefault(s.symbol, []).append(s.total_score)
            # 只保留最近10次
            if len(self._history[s.symbol]) > 10:
                self._history[s.symbol] = self._history[s.symbol][-10:]

        self._last_scan = datetime.now()

        return ScanResult(
            scan_id=f"scan_{int(datetime.now().timestamp())}",
            total_symbols_scanned=len(all_symbols),
            qualified_symbols=qualified,
            new_opportunities=new_opps,
            removed_symbols=removed,
        )

    def _evaluate_symbol_sync(self, symbol: str) -> SymbolScore:
        """评估单个交易对（同步版本，用于线程池并发）"""
        market = self._get_market_data(symbol)
        klines = self._get_klines(symbol, '1h', 100)

        if klines is None or (isinstance(klines, pd.DataFrame) and len(klines) < 24):
            return SymbolScore(
                symbol=symbol, total_score=0,
                volume_score=0, volatility_score=0,
                trend_score=0, funding_score=0, anomaly_score=0,
            )

        close = klines['close'].values
        volume = klines['volume'].values if 'volume' in klines.columns else np.zeros(len(close))

        vol_24h = float(np.sum(volume[-24:]) * close[-1])
        volume_score = 0.0 if vol_24h < self.MIN_24H_VOLUME else min(float(np.log10(vol_24h / self.MIN_24H_VOLUME)) * 10, 25.0)

        returns = np.diff(np.log(close[-24:]))
        volatility = float(np.std(returns) * np.sqrt(24))
        vol_score = 0.0 if volatility < self.MIN_VOLATILITY else min(volatility / 0.1 * 25, 25.0)

        sma20 = float(np.mean(close[-20:]))
        sma50 = float(np.mean(close[-min(50, len(close)):]))
        trend = abs(sma20 - sma50) / (sma50 + 1e-10)
        trend_score = min(trend / 0.05 * 25, 25.0)

        funding_rate = float(market.get('funding_rate', 0)) if market else 0.0
        funding_score = min(abs(funding_rate) * 1000, 15.0)

        vol_mean = float(np.mean(volume[-24:]))
        vol_std = float(np.std(volume[-24:])) + 1e-10
        vol_z = float((volume[-1] - vol_mean) / vol_std)
        anomaly_score = min(abs(vol_z) / 3 * 10, 10.0)

        total = volume_score + vol_score + trend_score + funding_score + anomaly_score

        reasons = []
        if volume_score > 15:
            reasons.append(f"high_volume(${vol_24h/1e6:.1f}M)")
        if vol_score > 15:
            reasons.append(f"high_volatility({volatility:.1%})")
        if trend_score > 15:
            reasons.append(f"strong_trend({trend:.1%})")
        if funding_score > 10:
            reasons.append(f"funding_opportunity({funding_rate:.4%})")

        return SymbolScore(
            symbol=symbol,
            total_score=total,
            volume_score=volume_score,
            volatility_score=vol_score,
            trend_score=trend_score,
            funding_score=funding_score,
            anomaly_score=anomaly_score,
            reasons=reasons,
        )

    async def _evaluate_symbol(self, symbol: str) -> SymbolScore:
        """评估单个交易对（async 兼容包装）"""
        return self._evaluate_symbol_sync(symbol)
