"""
Unified Data Pool - 统一数据池

ATAS系统的核心数据层，确保所有模块在同一时间点使用一致的数据：
1. 统一的数据快照机制
2. 各模块共享同一数据源
3. 避免因数据时差导致的决策不一致

Author: ATAS System
"""

import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _as_utc_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """统一为 UTC aware，避免 naive/aware 比较异常。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 120) -> int:
    try:
        return max(min_value, min(int(os.getenv(name, str(default))), max_value))
    except Exception:
        return default


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


# 全局并行拉取线程池：外部 API（Binance/Coinalyze/Hyperliquid 等）按 symbol 并发。
# 币种从用户配置动态扩展后，8 个线程容易把衍生品/情报采集卡在 30s 边界。
_PARALLEL_CAPTURE_WORKERS = _env_int("UNIFIED_DATA_POOL_CAPTURE_WORKERS", 16, min_value=4, max_value=32)
_parallel_capture_pool = ThreadPoolExecutor(
    max_workers=_PARALLEL_CAPTURE_WORKERS,
    thread_name_prefix="udp-capture",
)


@dataclass
class MarketSnapshot:
    """单一交易对的市场快照"""
    symbol: str
    price: float = 0.0
    price_24h_change: float = 0.0
    volume_24h: float = 0.0
    funding_rate: float = 0.0
    open_interest: float = 0.0
    timestamp: float = 0.0


@dataclass
class AccountSnapshot:
    """账户状态快照"""
    account_id: int
    environment: str
    total_equity: float = 0.0
    available_balance: float = 0.0
    used_margin: float = 0.0
    margin_usage_pct: float = 0.0
    positions: List[Dict] = field(default_factory=list)
    timestamp: float = 0.0


@dataclass
class StrategySnapshot:
    """策略分析快照 - 来自策略编排层"""
    # 中长期规划
    market_cycle: str = "unknown"
    cycle_confidence: float = 0.0
    position_bias: str = "neutral"  # long/short/neutral
    recommended_leverage: float = 10.0
    max_position_size: float = 0.25
    max_daily_loss_pct: float = 0.05
    key_support: float = 0.0
    key_resistance: float = 0.0
    regime_warning: bool = False
    
    # 短期战术
    tactical_action: str = "wait"  # enter_long/enter_short/exit/hold/wait
    tactical_confidence: float = 0.0
    entry_timing: str = "standard"
    suggested_stop_loss: float = 0.0
    suggested_take_profit: float = 0.0
    market_condition: str = "quiet"
    
    # 因子信号
    factors: Dict[str, float] = field(default_factory=dict)
    active_signals: List[Dict] = field(default_factory=list)


@dataclass
class UnifiedSnapshot:
    """统一数据快照 - 所有模块共享"""
    snapshot_id: str = ""
    timestamp: float = 0.0
    timestamp_iso: str = ""
    
    # 市场数据
    markets: Dict[str, MarketSnapshot] = field(default_factory=dict)
    
    # 账户数据
    accounts: Dict[Tuple[int, str], AccountSnapshot] = field(default_factory=dict)
    
    # 策略分析
    strategy: StrategySnapshot = field(default_factory=StrategySnapshot)
    
    # K线数据缓存
    klines: Dict[Tuple[str, str], pd.DataFrame] = field(default_factory=dict)
    
    # 技术指标缓存
    indicators: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # per-symbol 长线规划结果（替代全局单份 strategy 中的长线部分）
    per_symbol_planning: Dict[str, Any] = field(default_factory=dict)

    # ===== 智能多周期扩展 =====
    news_signals: List[Dict] = field(default_factory=list)
    news_by_symbol: Dict[str, List[Dict]] = field(default_factory=dict)
    whale_signals: Dict[str, Any] = field(default_factory=dict)
    derivatives_snapshot: Dict[str, Any] = field(default_factory=dict)
    sentiment_index: Dict[str, Any] = field(default_factory=dict)
    intelligence_by_symbol: Dict[str, str] = field(default_factory=dict)
    data_completeness: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # [2026-07-10 Phase1] 全市场聚合数据（多所盘口 + OI/费率）
    aggregate_orderbook: Dict[str, Any] = field(default_factory=dict)
    aggregate_market: Dict[str, Any] = field(default_factory=dict)


class UnifiedDataPool:
    """
    统一数据池
    
    核心功能：
    1. capture_snapshot() - 在决策前捕获统一时间点的数据快照
    2. get_snapshot() - 获取当前有效快照
    3. 各模块通过统一接口获取数据，确保一致性
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        self._current_snapshot: Optional[UnifiedSnapshot] = None
        self._snapshot_lock = threading.Lock()
        self._snapshot_ttl = 5.0  # 快照有效期5秒
        
        # 历史快照（用于分析）
        self._snapshot_history: deque = deque(maxlen=20)
        
        logger.info("[UnifiedDataPool] 初始化完成")
    
    def capture_snapshot(
        self,
        symbols: List[str],
        account_id: Optional[int] = None,
        environment: str = "testnet",
        include_klines: bool = True,
        include_strategy: bool = True,
        light_mode: bool = False,
    ) -> UnifiedSnapshot:
        """
        捕获统一数据快照
        
        在每次AI决策前调用，确保所有数据来自同一时间点
        
        Args:
            symbols: 要获取数据的交易对列表
            account_id: 账户ID（可选）
            environment: 交易环境
            include_klines: 是否包含K线数据
            include_strategy: 是否包含策略分析
            
        Returns:
            UnifiedSnapshot 统一数据快照
        """
        import uuid
        
        snapshot = UnifiedSnapshot(
            snapshot_id=str(uuid.uuid4())[:8],
            timestamp=time.time(),
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )
        
        logger.info(f"[UnifiedDataPool] 开始捕获数据快照 {snapshot.snapshot_id}")
        
        # 1. 捕获市场数据
        snapshot.markets = self._capture_market_data(symbols, environment)
        
        # 2. 捕获账户数据
        if account_id:
            snapshot.accounts = self._capture_account_data(account_id, environment)
        
        # 3. 捕获K线数据
        if include_klines:
            snapshot.klines = self._capture_klines(symbols, environment)
            self._fill_market_prices_from_klines(snapshot, symbols)
        
        # 4. 技术指标（与策略分析解耦：编排器/快评必须能读到 RSI/MACD/4h/1d）
        if include_klines and snapshot.klines:
            snapshot.indicators = self._capture_indicators(symbols, snapshot.klines)

        # 5. 策略分析 + 长线 per-symbol 规划（较重，可单独关闭）
        if include_strategy and snapshot.klines:
            snapshot.strategy = self._capture_strategy_analysis(
                symbols, snapshot.klines, snapshot.markets, snapshot.accounts,
                snapshot=snapshot,
            )
        
        if light_mode:
            snapshot.news_by_symbol = {}
            snapshot.news_signals = []
            snapshot.whale_signals = {}
            snapshot.derivatives_snapshot = self._capture_derivatives(symbols)
            snapshot.sentiment_index = {}
            snapshot.intelligence_by_symbol = {
                sym: "=== INTELLIGENCE TRADING SIGNAL ===\nstatus: skipped_light_mode"
                for sym in symbols
            }
            logger.info(
                f"[UnifiedDataPool] 轻量快照 {snapshot.snapshot_id}: "
                f"跳过新闻/鲸鱼/长线规划/情报(prompt)"
            )
        else:
            snapshot.news_by_symbol = self._capture_news_by_symbol(symbols)
            snapshot.news_signals = []
            for sym in symbols:
                for item in snapshot.news_by_symbol.get(sym, [])[:5]:
                    row = dict(item)
                    row.setdefault("symbol", sym)
                    snapshot.news_signals.append(row)
            snapshot.whale_signals = self._capture_whale_signals(symbols)
            snapshot.derivatives_snapshot = self._capture_derivatives(symbols)
            snapshot.sentiment_index = self._capture_sentiment(symbols)
            snapshot.intelligence_by_symbol = self._capture_intelligence_prompts(symbols)

        # [2026-07-10 Phase1] 全市场聚合数据采集（多所盘口 + OI/费率）
        snapshot.aggregate_orderbook = self._capture_aggregate_orderbook(symbols)
        snapshot.aggregate_market = self._capture_aggregate_market(symbols)

        # 11. 指标并入衍生品/市场字段 + 完整性审计
        self._enrich_indicators_from_context(snapshot, symbols)
        snapshot.data_completeness = self.audit_snapshot_completeness(snapshot, symbols)
        _incomplete = [
            s for s, rep in snapshot.data_completeness.items()
            if not rep.get("ok", True)
        ]
        if _incomplete:
            logger.warning(
                f"[UnifiedDataPool] 快照 {snapshot.snapshot_id} 数据不完整: "
                f"{','.join(_incomplete)}"
            )

        # 保存当前快照
        with self._snapshot_lock:
            self._current_snapshot = snapshot
            self._snapshot_history.append(snapshot)
        
        logger.info(
            f"[UnifiedDataPool] 快照 {snapshot.snapshot_id} 捕获完成: "
            f"{len(snapshot.markets)} 市场, {len(snapshot.klines)} K线集, "
            f"{len(snapshot.news_signals)} 新闻"
        )
        
        return snapshot
    
    def get_snapshot(self, max_age: Optional[float] = None) -> Optional[UnifiedSnapshot]:
        """获取当前有效快照"""
        if self._read_snapshot_store_enabled():
            try:
                store_snapshot = self._get_snapshot_store_unified(max_age=max_age)
                if store_snapshot is not None:
                    return store_snapshot
            except Exception as e:
                logger.warning("[UnifiedDataPool] SnapshotStore 读取失败，回退旧快照: %s", e)

        with self._snapshot_lock:
            if not self._current_snapshot:
                return None
            
            age = time.time() - self._current_snapshot.timestamp
            if max_age and age > max_age:
                return None
            if age > self._snapshot_ttl:
                return None
                
            return self._current_snapshot

    @staticmethod
    def _read_snapshot_store_enabled() -> bool:
        return (
            os.getenv("UNIFIED_DATA_POOL_READ_SNAPSHOT_STORE", "false").lower() in {"1", "true", "yes", "on"}
            or os.getenv("QAA_READ_SNAPSHOT_STORE", "false").lower() in {"1", "true", "yes", "on"}
        )

    def _get_snapshot_store_unified(self, max_age: Optional[float] = None) -> Optional[UnifiedSnapshot]:
        """把 SnapshotStore 的轻量快照适配成旧 UnifiedSnapshot 结构。"""
        from backend.services.snapshot_store import snapshot_store

        stored = snapshot_store.get_latest(max_age=max_age)
        if stored is None:
            return None

        try:
            ts = datetime.fromisoformat(stored.as_of.replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = time.time()

        unified = UnifiedSnapshot(
            snapshot_id=stored.snapshot_id,
            timestamp=ts,
            timestamp_iso=stored.as_of,
        )

        latest_by_symbol: Dict[str, Dict[str, Any]] = {}
        for key, rows in (stored.klines or {}).items():
            if not rows:
                continue
            parts = str(key).split(":")
            if len(parts) >= 3:
                symbol, period = parts[-2].upper(), parts[-1]
            else:
                continue
            df = pd.DataFrame(rows)
            if not df.empty:
                unified.klines[(symbol, period)] = df
                last = rows[-1] if isinstance(rows[-1], dict) else {}
                latest_by_symbol[symbol] = last

        for symbol, last in latest_by_symbol.items():
            price = float(last.get("close") or last.get("close_price") or 0)
            volume = float(last.get("volume") or 0)
            timestamp = float(last.get("timestamp") or ts)
            unified.markets[symbol] = MarketSnapshot(
                symbol=symbol,
                price=price,
                volume_24h=volume,
                timestamp=timestamp,
            )
            unified.indicators[symbol] = {
                "last_price": price,
                "close": price,
                "snapshot_source": "snapshot_store",
            }
            unified.data_completeness[symbol] = {
                "ok": bool(price),
                "source": "snapshot_store",
                "missing": [] if price else ["price"],
            }

        return unified
    
    def _capture_market_data(
        self, 
        symbols: List[str],
        environment: str
    ) -> Dict[str, MarketSnapshot]:
        """捕获市场数据 — 纯 MarketDataHub 路径（不读 DB 指标）"""
        markets = {}

        try:
            from backend.services.market_price_service import get_market_snapshots

            hub_snaps = get_market_snapshots(symbols)
            for symbol in symbols:
                sym_key = symbol.upper()
                snap = hub_snaps.get(sym_key) or hub_snaps.get(symbol) or {}
                price = float(snap.get("price", 0) or 0)
                funding = float(snap.get("funding_rate", 0) or 0)
                oi = float(snap.get("open_interest", 0) or 0)
                pc24 = float(snap.get("price_24h_change_pct", 0) or 0)
                vol24 = float(snap.get("volume_24h", 0) or 0)

                if not price:
                    try:
                        from services.price_cache import get_cached_price
                        price = float(get_cached_price(symbol, "CRYPTO", environment) or 0)
                    except Exception:
                        price = 0.0

                markets[symbol] = MarketSnapshot(
                    symbol=symbol,
                    price=price,
                    price_24h_change=pc24,
                    volume_24h=vol24,
                    funding_rate=funding,
                    open_interest=oi,
                    timestamp=time.time(),
                )

        except Exception as e:
            logger.error(f"捕获市场数据失败: {e}")

        return markets

    def _fill_market_prices_from_klines(self, snapshot: UnifiedSnapshot, symbols: List[str]) -> None:
        """Use already-loaded K-line close prices as a non-blocking market price fallback."""
        preferred_tfs = ("5m", "15m", "1h", "4h", "1d")
        for symbol in symbols:
            market = snapshot.markets.setdefault(symbol, MarketSnapshot(symbol=symbol))
            if market.price:
                continue
            for tf in preferred_tfs:
                df = snapshot.klines.get((symbol, tf))
                if df is None or not hasattr(df, "empty") or df.empty or "close" not in df.columns:
                    continue
                try:
                    close = float(df["close"].dropna().iloc[-1])
                    if close:
                        market.price = close
                        market.timestamp = time.time()
                        break
                except Exception:
                    continue
    
    def _capture_account_data(
        self,
        account_id: int,
        environment: str
    ) -> Dict[Tuple[int, str], AccountSnapshot]:
        """捕获账户数据（仅 HyperLiquid，Binance 已移除）"""
        accounts = {}
        try:
            from services.hyperliquid_cache import get_cached_account_state, get_cached_positions

            state_entry = get_cached_account_state(account_id, environment, max_age_seconds=10)
            positions_entry = get_cached_positions(account_id, environment, max_age_seconds=10)

            if state_entry:
                state = state_entry["data"]
                snapshot = AccountSnapshot(
                    account_id=account_id,
                    environment=environment,
                    total_equity=float(state.get("total_equity", 0) or 0),
                    available_balance=float(state.get("available_balance", 0) or 0),
                    used_margin=float(state.get("used_margin", 0) or 0),
                    margin_usage_pct=float(state.get("margin_usage_percent", 0) or 0),
                    positions=positions_entry["data"] if positions_entry else [],
                    timestamp=time.time(),
                )
                accounts[(account_id, environment)] = snapshot
        except Exception as e:
            logger.error(f"捕获账户数据失败: {e}")
        return accounts

    def _capture_klines(
        self,
        symbols: List[str],
        environment: str
    ) -> Dict[Tuple[str, str], pd.DataFrame]:
        """捕获K线数据（含 1d 日线用于大趋势判定）"""
        klines = {}
        timeframes = ["5m", "15m", "1h", "4h", "1d", "1w"]

        # 预取每个 symbol 的 funding_rate，用于注入 K线 DataFrame
        funding_rates: Dict[str, float] = {}
        try:
            from services.market_flow_indicators import get_indicator_value

            from backend.database.connection import MarketSessionLocal
            with MarketSessionLocal() as db:
                for symbol in symbols:
                    try:
                        rate = get_indicator_value(db, symbol, "FUNDING", "1h")
                        funding_rates[symbol] = float(rate) if rate is not None else 0.0
                    except Exception:
                        funding_rates[symbol] = 0.0
        except Exception as e:
            logger.debug(f"预取 funding_rate 失败: {e}")
            for symbol in symbols:
                funding_rates[symbol] = 0.0

        # 预取衍生品数据（OI、多空比）— 增强项，不允许阻塞主 K 线快照。
        derivatives_data: Dict[str, Dict[str, float]] = {}
        try:
            from backend.config.settings import UNIFIED_DATA_POOL_KLINE_DERIVATIVES_PREFETCH
            _deriv_prefetch = UNIFIED_DATA_POOL_KLINE_DERIVATIVES_PREFETCH
        except Exception:
            _deriv_prefetch = _env_bool("UNIFIED_DATA_POOL_KLINE_DERIVATIVES_PREFETCH", "true")
        if not _deriv_prefetch:
            for symbol in symbols:
                derivatives_data[symbol] = {'oi': 0.0, 'long_short_ratio': 1.0}
        else:
            try:
                from services.derivatives_analytics_service import derivatives_analytics

                def _deriv_one(sym: str):
                    try:
                        snap = derivatives_analytics.get_snapshot(sym)
                        return sym, {
                            'oi': snap.oi_total if snap.oi_total else 0.0,
                            'long_short_ratio': snap.long_short_ratio if snap.long_short_ratio else 1.0,
                        }
                    except Exception:
                        return sym, {'oi': 0.0, 'long_short_ratio': 1.0}

                t0 = time.time()
                futures = [_parallel_capture_pool.submit(_deriv_one, s) for s in symbols]
                try:
                    for f in as_completed(futures, timeout=_env_int("UNIFIED_DATA_POOL_KLINE_DERIVATIVES_TIMEOUT", 20, min_value=5, max_value=90)):
                        try:
                            sym, data = f.result(timeout=5)
                            derivatives_data[sym] = data
                        except Exception:
                            pass
                except FuturesTimeoutError:
                    logger.warning("[UnifiedDataPool] K线衍生品预取超时，使用中性占位")
                for s in symbols:
                    derivatives_data.setdefault(s, {'oi': 0.0, 'long_short_ratio': 1.0})
                logger.debug(
                    f"[UnifiedDataPool] K线衍生品预取: {len(derivatives_data)}/{len(symbols)}, "
                    f"耗时={(time.time()-t0)*1000:.0f}ms"
                )
            except Exception as e:
                logger.debug(f"预取衍生品数据失败: {e}")
                for symbol in symbols:
                    derivatives_data[symbol] = {'oi': 0.0, 'long_short_ratio': 1.0}

        # 预取链上+宏观数据，用于注入 K线 DataFrame
        onchain_data: Dict[str, Dict[str, Any]] = {}
        if _env_bool("UNIFIED_DATA_POOL_KLINE_ONCHAIN_ENRICHMENT", "true"):
            try:
                from services.onchain_data_collector import onchain_collector
                onchain_data = onchain_collector.collect_all(symbols)
            except Exception as e:
                logger.debug(f"预取链上/宏观数据失败: {e}")

        # 预取社交情绪数据
        # [2026-08-17 删除] social_sentiment_collector 已移除（CryptoPanic 403 失效、
        # token 明文进日志、与 news_intelligence 重复采集）。
        social_data: Dict[str, Dict[str, Any]] = {}

        fear_greed_daily: Dict[int, float] = {}
        if _env_bool("UNIFIED_DATA_POOL_KLINE_AUX_ENRICHMENT", "true"):
            try:
                from backend.services.kline_enrichment_service import (
                    fetch_fear_greed_daily_history,
                    record_aux_snapshots,
                )
                fear_greed_daily = fetch_fear_greed_daily_history(60)
                from backend.database.connection import MarketSessionLocal as _MktSL
                with _MktSL() as _aux_db:
                    record_aux_snapshots(_aux_db, symbols, onchain_data, social_data)
            except Exception as e:
                logger.debug(f"链上/社交时间序列记录失败: {e}")

        try:
            from services.kline_data_service import kline_service

            from backend.database.connection import MarketSessionLocal as _MktSL2
            from backend.services.kline_enrichment_service import enrich_kline_dataframe

            # [2026-07-11 修复] 原实现用同一个 `with _MktSL2() as flow_db:` 包住整个
            # symbols × timeframes 双重循环，循环内部含交易所 API 回退请求（可能因限流
            # retry 耗时数秒）——导致这一个数据库事务被整个循环的网络耗时"撑住"，
            # 观测到最长挂起 549s（9分钟+），期间持有的行锁/连接把 kline 采集器、
            # 交易主循环等其他查询一起卡死（DB LeakGuard 报警的最大来源）。
            # 修复：DB 会话只在真正需要落库/读库的 enrich_kline_dataframe 调用瞬间
            # 短暂打开，网络请求和 CPU 计算都在会话之外完成，事务生命周期从"分钟级"
            # 降到"毫秒级"。
            for symbol in symbols:
                for tf in timeframes:
                    try:
                        _min_bars = {"1w": 8, "1d": 20}.get(tf, 20)
                        raw = kline_service.get_klines_from_db(symbol, tf, count=200)
                        # API 回退：DB 无数据时从交易所拉取并持久化（网络调用，不持有DB会话）
                        if not raw or len(raw) < _min_bars:
                            try:
                                from backend.services.market_data import get_kline_data
                                raw = get_kline_data(symbol, period=tf, count=200)
                            except Exception:
                                pass
                        if raw and len(raw) >= _min_bars:
                            df = pd.DataFrame(raw)
                            if not df.empty:
                                fr_default = funding_rates.get(symbol, 0.0)
                                df['funding_rate'] = self._fetch_funding_rate_series(
                                    symbol, df, default_rate=fr_default
                                )
                                dd = derivatives_data.get(symbol, {})
                                df['oi'] = dd.get('oi', 0.0)
                                df['long_short_ratio'] = dd.get('long_short_ratio', 1.0)

                                oc = onchain_data.get(symbol, {})
                                sc = social_data.get(symbol, {})
                                with _MktSL2() as flow_db:
                                    df = enrich_kline_dataframe(
                                        flow_db,
                                        symbol,
                                        df,
                                        tf,
                                        scalar_onchain=oc,
                                        scalar_social=sc,
                                        fear_greed_daily=fear_greed_daily,
                                    )
                                klines[(symbol, tf)] = df
                    except Exception as e:
                        logger.debug(f"获取 {symbol} {tf} K线失败: {e}")

        except Exception as e:
            logger.error(f"捕获K线数据失败: {e}")

        return klines

    # =============================================
    # P2 M-14: 多频率K线并行采集 + 对齐
    # =============================================
    def get_multi_freq_klines(
        self,
        symbol: str,
        timeframes: list = None,
        environment: str = "mainnet",
        count: int = 200,
    ) -> Dict[str, list]:
        """
        并行获取多周期K线并返回对齐后的 dict。

        Args:
            symbol: 交易对
            timeframes: 需要的时间周期列表, 默认 ["15m", "1h", "4h"]
            environment: 交易环境
            count: 每周期K线条数

        Returns:
            {
                "15m": [kline_dict, ...],
                "1h": [kline_dict, ...],
                "4h": [kline_dict, ...],
                "aligned_ts": int,  # 对齐时间戳
                "alignment_status": "ok" | "partial" | "mismatch",
            }
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if timeframes is None:
            timeframes = ["15m", "1h", "4h"]

        aligned_ts = int(time.time())
        result: Dict[str, list] = {}

        def _fetch_one(tf: str) -> tuple:
            try:
                raw = self.get_kline_series(symbol, interval=tf, limit=count)
                if raw:
                    # 统一转换为 dict 格式
                    if isinstance(raw[0], dict):
                        klist = raw
                    else:
                        klist = [
                            {
                                "timestamp": int(getattr(k, "timestamp", 0) or 0),
                                "open": float(getattr(k, "open", 0) or 0),
                                "high": float(getattr(k, "high", 0) or 0),
                                "low": float(getattr(k, "low", 0) or 0),
                                "close": float(getattr(k, "close", 0) or 0),
                                "volume": float(getattr(k, "volume", 0) or 0),
                            }
                            for k in raw
                        ]
                    return tf, klist
                return tf, []
            except Exception as e:
                logger.debug(f"[UnifiedDataPool] 多频率获取 {symbol}/{tf} 失败: {e}")
                return tf, []

        # 并行拉取
        with ThreadPoolExecutor(max_workers=min(len(timeframes), 4)) as executor:
            futures = {executor.submit(_fetch_one, tf): tf for tf in timeframes}
            for future in as_completed(futures, timeout=15):
                try:
                    tf, klist = future.result(timeout=10)
                    result[tf] = klist
                except Exception:
                    tf = futures[future]
                    result[tf] = []

        # 确保所有周期都有 key
        for tf in timeframes:
            if tf not in result:
                result[tf] = []

        # 对齐检查: 比较各周期最新K线时间戳
        latest_ts = {}
        for tf in timeframes:
            klist = result.get(tf, [])
            if klist:
                latest_ts[tf] = klist[-1].get("timestamp", 0) if isinstance(klist[-1], dict) else 0

        if not latest_ts:
            alignment = "empty"
        elif len(latest_ts) == len(timeframes):
            ts_values = list(latest_ts.values())
            max_gap = max(ts_values) - min(ts_values)
            # 允许最大 2 倍最长周期的时间偏差
            max_allowed_gap = {"15m": 900, "1h": 3600, "4h": 14400}.get(
                max(timeframes, key=lambda x: {"15m": 0, "1h": 1, "4h": 2}.get(x, 0)), 3600
            ) * 2
            if max_gap <= max_allowed_gap:
                alignment = "ok"
            else:
                alignment = "mismatch"
        else:
            alignment = "partial"

        result["aligned_ts"] = aligned_ts
        result["alignment_status"] = alignment

        logger.debug(
            f"[UnifiedDataPool] 多频率K线 {symbol}: "
            f"counts={[(tf, len(result.get(tf, []))) for tf in timeframes]}, "
            f"align={alignment}"
        )

        return result

    def _capture_strategy_analysis(
        self,
        symbols: List[str],
        klines: Dict[Tuple[str, str], pd.DataFrame],
        markets: Dict[str, MarketSnapshot],
        accounts: Optional[Dict] = None,
        snapshot: Optional['UnifiedSnapshot'] = None,
    ) -> StrategySnapshot:
        """
        捕获策略分析 — 按周期分离数据

        * 长线规划器：per-symbol 运行（优先 1d → 4h → 1h）
        * 短线战术器：优先使用 15m K线（回退 1h）
        * 长线输出的 position_bias / risk_budget 作为硬约束注入短线
        * 账户余额从真实 AccountSnapshot 读取
        """
        strategy = StrategySnapshot()

        try:
            primary_symbol = symbols[0] if symbols else "BTC"

            # ---- 解析真实账户余额 ----
            real_balance = 100.0  # 安全兜底
            if accounts:
                for _key, acct in accounts.items():
                    if acct.total_equity > 0:
                        real_balance = acct.total_equity
                        break

            # ---- per-symbol 长线规划 ----
            per_symbol_planning = {}
            _strategic_ctx = None
            try:
                from backend.services.strategic_analyst.engine import get_strategic_engine
                _strategic_ctx = get_strategic_engine().get_strategic_context_for_planner()
            except Exception as _sc_err:
                logger.debug(f"[UnifiedDataPool] strategic_context 不可用: {_sc_err}")

            try:
                from services.strategy_orchestrator.long_term_planner import long_term_planner

                for sym in symbols:
                    # 优先 1d → 4h → 1h
                    lt_key = None
                    for tf in ["1d", "4h", "1h"]:
                        if (sym, tf) in klines:
                            lt_key = (sym, tf)
                            break
                    if lt_key is None:
                        continue

                    lt_df = klines[lt_key]
                    if lt_df is None or lt_df.empty:
                        continue

                    mkt = markets.get(sym, MarketSnapshot(symbol=sym))
                    funding_history = self._get_funding_history(sym, default_rate=mkt.funding_rate)
                    volume_df = lt_df[['volume']] if 'volume' in lt_df.columns else None
                    oi_df = self._build_oi_dataframe(sym, klines, mkt.open_interest)

                    try:
                        result = long_term_planner.plan(
                            klines=lt_df,
                            funding_history=funding_history,
                            volume_data=volume_df,
                            oi_data=oi_df,
                            account_balance=real_balance,
                            current_drawdown=0.0,
                            strategic_context=_strategic_ctx,
                        )
                        per_symbol_planning[sym] = result
                        logger.debug(
                            f"[LongTermPlanner|{lt_key[1]}] {sym}: "
                            f"周期={result.market_cycle.value}, 偏向={result.position_bias}, "
                            f"置信度={result.cycle_confidence:.1%}"
                        )
                    except Exception as plan_err:
                        logger.warning(f"[LongTermPlanner] {sym} 规划异常: {plan_err}")
                from backend.config.settings import STRICT_DATA_GATE
                if not STRICT_DATA_GATE:
                    for sym in symbols:
                        _plan = per_symbol_planning.get(sym)
                        if _plan is None:
                            continue
                        if _plan.position_bias not in ("neutral", None, ""):
                            continue
                        if _plan.cycle_confidence >= 0.25:
                            continue
                        try:
                            _fb_sym_key = None
                            for _tf in ["1d", "4h", "1h", "15m"]:
                                if (sym, _tf) in klines:
                                    _fb_sym_key = (sym, _tf)
                                    break
                            if _fb_sym_key is None:
                                continue
                            _fb_sym_df = klines[_fb_sym_key]
                            if _fb_sym_df is None or len(_fb_sym_df) < 20:
                                continue
                            _close_s = _fb_sym_df['close'].values.astype(float)
                            _ema20_s = float(np.convolve(_close_s, np.ones(20)/20, 'valid')[-1])
                            _ema5_s  = float(np.convolve(_close_s, np.ones(5)/5, 'valid')[-1])
                            _cur_s   = float(_close_s[-1])
                            if _cur_s > _ema20_s and _ema5_s > _ema20_s:
                                _plan.position_bias = "long"
                                logger.info(
                                    f"[LongTermPlanner] {sym} 低置信度 fallback → "
                                    f"bullish (cur={_cur_s:.4f} > ema20={_ema20_s:.4f})"
                                )
                            elif _cur_s < _ema20_s and _ema5_s < _ema20_s:
                                _plan.position_bias = "short"
                                logger.info(
                                    f"[LongTermPlanner] {sym} 低置信度 fallback → "
                                    f"bearish (cur={_cur_s:.4f} < ema20={_ema20_s:.4f})"
                                )
                        except Exception as _fb_sym_err:
                            logger.debug(f"[LongTermPlanner] {sym} fallback异常: {_fb_sym_err}")

            except Exception as e:
                logger.warning(f"per-symbol 长线规划导入失败: {e}")

            if snapshot is not None:
                snapshot.per_symbol_planning = per_symbol_planning

            # 兼容：用 primary_symbol 的结果填充全局 strategy（旧代码依赖）
            primary_plan = per_symbol_planning.get(primary_symbol)

            # ---- 选择长线 K 线 fallback（保持旧逻辑兼容） ----
            long_kline_key = (primary_symbol, "1d")
            if long_kline_key not in klines:
                long_kline_key = (primary_symbol, "4h")
            if long_kline_key not in klines:
                long_kline_key = (primary_symbol, "1h")
            if long_kline_key not in klines:
                long_kline_key = (primary_symbol, "15m")

            # ---- 选择短线 K 线（15m 优先，回退 1h） ----
            short_kline_key = (primary_symbol, "15m")
            if short_kline_key not in klines:
                short_kline_key = (primary_symbol, "1h")

            if long_kline_key not in klines and short_kline_key not in klines:
                logger.warning(f"没有找到 {primary_symbol} 的任何K线数据，跳过策略分析")
                return strategy

            # ========== 中长期规划（使用 per-symbol 结果） ==========
            lt_position_bias = "neutral"
            lt_max_pos = 0.30
            if primary_plan is not None:
                strategy.market_cycle = primary_plan.market_cycle.value
                strategy.cycle_confidence = primary_plan.cycle_confidence
                strategy.position_bias = primary_plan.position_bias
                strategy.recommended_leverage = primary_plan.recommended_leverage
                strategy.max_position_size = primary_plan.risk_budget.max_position_size
                strategy.max_daily_loss_pct = primary_plan.risk_budget.max_daily_loss_pct
                strategy.regime_warning = primary_plan.regime_transition_warning

                if primary_plan.key_levels:
                    strategy.key_support = primary_plan.key_levels.get("nearest_support", 0)
                    strategy.key_resistance = primary_plan.key_levels.get("nearest_resistance", 0)

                lt_position_bias = primary_plan.position_bias or "neutral"
                lt_max_pos = primary_plan.risk_budget.max_position_size

                logger.info(
                    f"[LongTermPlanner] {primary_symbol}: "
                    f"周期={strategy.market_cycle}, 偏向={lt_position_bias}, "
                    f"风险预算仓位={lt_max_pos:.0%}, "
                    f"置信度={strategy.cycle_confidence:.1%}"
                )

            from backend.config.settings import STRICT_DATA_GATE
            if not STRICT_DATA_GATE and lt_position_bias == "neutral" and strategy.cycle_confidence < 0.25:
                try:
                    _fb_key = long_kline_key if long_kline_key in klines else short_kline_key
                    _fb_df = klines.get(_fb_key)
                    if _fb_df is not None and len(_fb_df) >= 20:
                        _close = _fb_df['close'].values.astype(float)
                        _ema20 = float(np.convolve(_close, np.ones(20)/20, 'valid')[-1])
                        _ema5  = float(np.convolve(_close, np.ones(5)/5,  'valid')[-1])
                        _cur   = float(_close[-1])
                        if _cur > _ema20 and _ema5 > _ema20:
                            lt_position_bias = "bullish"
                            strategy.position_bias = "long"
                            logger.info(
                                f"[LongTermPlanner] {primary_symbol} 低置信度 fallback → "
                                f"bullish (cur={_cur:.4f} > ema20={_ema20:.4f})"
                            )
                        elif _cur < _ema20 and _ema5 < _ema20:
                            lt_position_bias = "bearish"
                            strategy.position_bias = "short"
                            logger.info(
                                f"[LongTermPlanner] {primary_symbol} 低置信度 fallback → "
                                f"bearish (cur={_cur:.4f} < ema20={_ema20:.4f})"
                            )
                except Exception as _fb_err:
                    logger.debug(f"[LongTermPlanner] fallback计算失败 {primary_symbol}: {_fb_err}")

            # ========== 短期战术（15m 数据 + 长线硬约束） ==========
            try:
                from services.strategy_orchestrator import ShortTermContext, get_short_term_tactician
                from services.strategy_orchestrator.short_term_tactician import TacticalConfig

                st_df = klines.get(short_kline_key)
                if st_df is None or st_df.empty:
                    raise ValueError(f"短线 K 线 {short_kline_key} 为空")

                close = st_df['close'].values.astype(float)
                high = st_df['high'].values.astype(float)
                low = st_df['low'].values.astype(float)
                current_price = float(close[-1]) if len(close) > 0 else 0.0

                ema_9 = self._ema(close, 9)[-1] if len(close) >= 9 else current_price
                ema_21 = self._ema(close, 21)[-1] if len(close) >= 21 else current_price

                # 真实 VWAP 计算（典型价 * 成交量 的累计比值）
                vwap = current_price
                if 'volume' in st_df.columns:
                    vol = st_df['volume'].values.astype(float)
                    typical = (high + low + close) / 3.0
                    cum_vol = np.cumsum(vol)
                    if cum_vol[-1] > 0:
                        vwap = float(np.cumsum(typical * vol)[-1] / cum_vol[-1])

                rsi = self._calculate_rsi(close, 14)
                macd, signal, histogram = self._calculate_macd(close)
                atr = self._calculate_atr(high, low, close, 14)

                context = ShortTermContext(
                    symbol=primary_symbol,
                    current_price=current_price,
                    vwap=vwap,
                    ema_9=ema_9,
                    ema_21=ema_21,
                    rsi=rsi,
                    macd=macd,
                    macd_signal=signal,
                    macd_histogram=histogram,
                    atr=atr,
                    atr_pct=atr / current_price if current_price > 0 else 0,
                )

                # 将长线规划结果注入短线配置（统一枚举 long_only/short_only/both）
                allowed_dir = "both"
                if lt_position_bias in ("long", "bullish"):
                    allowed_dir = "long_only"
                elif lt_position_bias in ("short", "bearish"):
                    allowed_dir = "short_only"

                # 不再修改单例 config（防并发串配置），而是创建临时 config 副本
                from backend.services.strategy_orchestrator.short_term_tactician import TacticalConfig
                tactician = get_short_term_tactician()
                _saved_config = tactician.config
                _temp_config = TacticalConfig(
                    allowed_direction=allowed_dir,
                    long_term_bias=lt_position_bias,
                    long_term_max_position=lt_max_pos,
                    min_confidence=_saved_config.min_confidence,
                    max_position_size=_saved_config.max_position_size,
                    rsi_overbought=_saved_config.rsi_overbought,
                    rsi_oversold=_saved_config.rsi_oversold,
                    rsi_extreme_high=_saved_config.rsi_extreme_high,
                    rsi_extreme_low=_saved_config.rsi_extreme_low,
                    sl_atr_multiple=_saved_config.sl_atr_multiple,
                    tp_atr_multiple=_saved_config.tp_atr_multiple,
                    volume_surge_threshold=_saved_config.volume_surge_threshold,
                    signal_valid_minutes=_saved_config.signal_valid_minutes,
                    min_holding_minutes=_saved_config.min_holding_minutes,
                )
                tactician.config = _temp_config
                try:
                    tactical_signal = tactician.analyze(context)
                finally:
                    tactician.config = _saved_config

                strategy.tactical_action = tactical_signal.action.value
                strategy.tactical_confidence = tactical_signal.confidence
                strategy.entry_timing = tactical_signal.entry_timing.value
                strategy.suggested_stop_loss = tactical_signal.stop_loss
                strategy.suggested_take_profit = tactical_signal.take_profit
                strategy.market_condition = context.market_condition.value

                logger.info(
                    f"[ShortTermTactician|15m] {primary_symbol}: "
                    f"动作={strategy.tactical_action}, 置信度={strategy.tactical_confidence:.1%}, "
                    f"方向约束={allowed_dir}, 市场状态={strategy.market_condition}"
                )

            except Exception as e:
                logger.warning(f"短期战术分析失败: {e}")

            if snapshot is not None and snapshot.indicators:
                strategy.factors = dict(snapshot.indicators.get(primary_symbol, {}))
                try:
                    from backend.services.intelligence_signal_engine import intelligence_signal_engine
                    _isig = intelligence_signal_engine.compute_trading_signal(primary_symbol)
                    strategy.active_signals = [{
                        "source": "intelligence_engine",
                        "direction": _isig.direction,
                        "confidence": _isig.confidence,
                        "risk_level": _isig.risk_level,
                    }]
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"策略分析失败: {e}")

        return strategy

    # ------------------------------------------------------------------
    # 辅助: funding_history
    # ------------------------------------------------------------------
    def _get_funding_history(self, symbol: str, default_rate: float = 0.0, limit: int = 50) -> list:
        """从 MarketAssetMetrics 取 funding 历史序列（非单点）。"""
        try:
            import time as _time

            from services.market_flow_indicators import _get_funding_data

            from backend.database.connection import MarketSessionLocal

            period = "1h"
            interval_ms = 3600 * 1000
            now_ms = int(_time.time() * 1000)
            with MarketSessionLocal() as db:
                data = _get_funding_data(db, symbol, period, interval_ms, now_ms)
                if data and data.get("last_5"):
                    hist = [float(x) for x in data["last_5"] if x is not None]
                    if hist:
                        return hist[-limit:]
                from services.market_flow_indicators import get_indicator_value
                rate = get_indicator_value(db, symbol, "FUNDING", "1h")
                if rate is not None:
                    return [float(rate)]
        except Exception as e:
            logger.debug(f"[UnifiedDataPool] funding_history {symbol}: {e}")
        return [default_rate] if default_rate else [0.0]

    # ------------------------------------------------------------------
    # 辅助: funding_rate 时序对齐
    # ------------------------------------------------------------------
    def _fetch_funding_rate_series(
        self, symbol: str, kline_df: pd.DataFrame, default_rate: float = 0.0
    ) -> pd.Series:
        """
        从 PerpFunding 表查询历史资金费率，使用 merge_asof 按时间对齐到 K 线。

        K-line timestamp 是秒级整数，PerpFunding timestamp 是毫秒级整数。
        返回与 kline_df 等长的 pd.Series（列名 funding_rate）。
        """
        if kline_df.empty or "timestamp" not in kline_df.columns:
            return pd.Series([default_rate] * len(kline_df), index=kline_df.index)

        try:
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import PerpFunding

            kline_ts_min = int(kline_df["timestamp"].min())
            kline_ts_max = int(kline_df["timestamp"].max())
            # PerpFunding 用毫秒，K线用秒
            ts_min_ms = kline_ts_min * 1000
            ts_max_ms = (kline_ts_max + 3600) * 1000  # 多取1小时确保覆盖

            with MarketSessionLocal() as db:
                rows = (
                    db.query(PerpFunding.timestamp, PerpFunding.funding_rate)
                    .filter(
                        PerpFunding.symbol == symbol.upper(),
                        PerpFunding.timestamp >= ts_min_ms,
                        PerpFunding.timestamp <= ts_max_ms,
                    )
                    .order_by(PerpFunding.timestamp.asc())
                    .all()
                )

            if not rows:
                logger.debug(
                    f"[UDP] {symbol} PerpFunding 无历史数据，回退标量 {default_rate}"
                )
                return pd.Series([default_rate] * len(kline_df), index=kline_df.index)

            # 构造 funding DataFrame（时间戳转为秒以对齐 K 线）
            fund_df = pd.DataFrame(rows, columns=["timestamp_ms", "funding_rate"])
            fund_df["timestamp"] = fund_df["timestamp_ms"] // 1000
            fund_df["funding_rate"] = fund_df["funding_rate"].astype(float)
            fund_df = fund_df.drop_duplicates(subset="timestamp", keep="last")
            fund_df = fund_df.sort_values("timestamp")

            # merge_asof: 每根 K 线取最近一条 funding 记录
            merged = pd.merge_asof(
                kline_df[["timestamp"]].sort_values("timestamp"),
                fund_df[["timestamp", "funding_rate"]],
                on="timestamp",
                direction="nearest",
                tolerance=28800,  # 8小时容差（funding 间隔通常8h）
            )

            series = merged["funding_rate"].fillna(default_rate)
            series.index = kline_df.index
            return series

        except Exception as e:
            logger.debug(f"[UDP] funding_rate_series {symbol}: {e}")
            return pd.Series([default_rate] * len(kline_df), index=kline_df.index)

    def _build_oi_dataframe(
        self,
        symbol: str,
        klines: Dict[Tuple[str, str], pd.DataFrame],
        fallback_oi: float = 0.0,
    ) -> Optional[pd.DataFrame]:
        """构建 LongTermPlanner 可用的 OI 序列。"""
        for tf in ("1d", "4h", "1h"):
            key = (symbol, tf)
            if key not in klines:
                continue
            df = klines[key]
            if df is None or df.empty:
                continue
            if "oi" in df.columns:
                oi_col = df["oi"].astype(float)
                if oi_col.notna().any() and oi_col.max() > 0:
                    return pd.DataFrame({"oi": oi_col.values})
        if fallback_oi and fallback_oi > 0:
            return pd.DataFrame({"oi": [float(fallback_oi)]})
        return None
    
    def _capture_indicators(
        self,
        symbols: List[str],
        klines: Dict[Tuple[str, str], pd.DataFrame]
    ) -> Dict[str, Dict[str, float]]:
        """从K线数据直接计算关键技术指标"""
        indicators = {}
        
        for symbol in symbols:
            kline_key = (symbol, "1h")
            if kline_key not in klines:
                kline_key = (symbol, "15m")
            if kline_key not in klines:
                continue
            
            try:
                df = klines[kline_key]
                close = df['close'].values.astype(float) if 'close' in df.columns else None
                high = df['high'].values.astype(float) if 'high' in df.columns else None
                low = df['low'].values.astype(float) if 'low' in df.columns else None
                open_vals = df['open'].values.astype(float) if 'open' in df.columns else None
                volume = df['volume'].values.astype(float) if 'volume' in df.columns else None
                
                if close is None or len(close) < 30:
                    continue
                
                rsi = self._calculate_rsi(close, 14)
                macd, macd_signal, macd_hist = self._calculate_macd(close)
                atr = self._calculate_atr(high, low, close, 14) if high is not None and low is not None else 0.0
                
                ema_9 = self._ema(close, 9)[-1] if len(close) >= 9 else close[-1]
                ema_21 = self._ema(close, 21)[-1] if len(close) >= 21 else close[-1]
                ema_trend = (ema_9 - ema_21) / ema_21 if ema_21 != 0 else 0
                
                bb_mid = np.mean(close[-20:])
                bb_std = np.std(close[-20:])
                bb_width = (2 * bb_std / bb_mid) if bb_mid > 0 else 0
                
                # ADX / +DI / -DI（1h 主周期）
                adx_val, plus_di_val, minus_di_val = self._calculate_adx(high, low, close, 14)

                # EMA50 用于多头/空头排列判断
                ema_50 = self._ema(close, 50)[-1] if len(close) >= 50 else close[-1]

                _pc_1h = 0.0
                _pc_24h = 0.0
                if len(close) >= 2 and close[-2] != 0:
                    _pc_1h = float((close[-1] - close[-2]) / close[-2])
                if len(close) >= 25 and close[-25] != 0:
                    _pc_24h = float((close[-1] - close[-25]) / close[-25])

                try:
                    from backend.database.connection import MarketSessionLocal
                    from backend.services.kline_enrichment_service import (
                        capture_flow_indicators_for_symbol,
                    )
                    with MarketSessionLocal() as _fdb:
                        _flow = capture_flow_indicators_for_symbol(_fdb, symbol)
                except Exception:
                    _flow = {}

                indicators[symbol] = {
                    "rsi": round(rsi, 2),
                    "macd": round(macd, 6),
                    "macd_signal": round(macd_signal, 6),
                    "macd_histogram": round(macd_hist, 6),
                    "atr": round(atr, 4),
                    "ema_trend": round(ema_trend, 6),
                    "bb_width": round(bb_width, 6),
                    "close": round(float(close[-1]), 2),
                    "adx": round(adx_val, 2),
                    "plus_di": round(plus_di_val, 2),
                    "minus_di": round(minus_di_val, 2),
                    "ema_9": round(float(ema_9), 6),
                    "ema_21": round(float(ema_21), 6),
                    "ema_50": round(float(ema_50), 6),
                    "price_change_1h": round(_pc_1h, 6),
                    "price_change_24h": round(_pc_24h, 6),
                    **_flow,
                }

                # 4h ADX / ATR / EMA — 供中期趋势和宽止损使用
                key_4h = (symbol, "4h")
                if key_4h in klines:
                    df4 = klines[key_4h]
                    c4 = df4['close'].values.astype(float) if 'close' in df4.columns else None
                    h4 = df4['high'].values.astype(float) if 'high' in df4.columns else None
                    l4 = df4['low'].values.astype(float) if 'low' in df4.columns else None
                    if c4 is not None and h4 is not None and l4 is not None and len(c4) >= 30:
                        a4, pd4, md4 = self._calculate_adx(h4, l4, c4, 14)
                        atr4 = self._calculate_atr(h4, l4, c4, 14)
                        e9_4 = self._ema(c4, 9)[-1] if len(c4) >= 9 else c4[-1]
                        e21_4 = self._ema(c4, 21)[-1] if len(c4) >= 21 else c4[-1]
                        e50_4 = self._ema(c4, 50)[-1] if len(c4) >= 50 else c4[-1]
                        indicators[symbol]["adx_4h"] = round(a4, 2)
                        indicators[symbol]["plus_di_4h"] = round(pd4, 2)
                        indicators[symbol]["minus_di_4h"] = round(md4, 2)
                        indicators[symbol]["atr_4h"] = round(atr4, 4)
                        indicators[symbol]["ema_9_4h"] = round(float(e9_4), 6)
                        indicators[symbol]["ema_21_4h"] = round(float(e21_4), 6)
                        indicators[symbol]["ema_50_4h"] = round(float(e50_4), 6)
                        # 4h RSI/MACD — 供编排器中期动量判断使用（此前只有1h，中期动量周期缺失）
                        try:
                            r4 = self._calculate_rsi(c4, 14)
                            m4, _ms4, mh4 = self._calculate_macd(c4)
                            indicators[symbol]["rsi_4h"] = round(float(r4), 2)
                            indicators[symbol]["macd_4h"] = round(float(m4), 6)
                            indicators[symbol]["macd_hist_4h"] = round(float(mh4), 6)
                        except Exception:
                            pass

                # 1d ADX / ATR / EMA — 供大趋势判定使用
                key_1d = (symbol, "1d")
                if key_1d in klines:
                    df1d = klines[key_1d]
                    c1d = df1d['close'].values.astype(float) if 'close' in df1d.columns else None
                    h1d = df1d['high'].values.astype(float) if 'high' in df1d.columns else None
                    l1d = df1d['low'].values.astype(float) if 'low' in df1d.columns else None
                    if c1d is not None and h1d is not None and l1d is not None and len(c1d) >= 20:
                        a1d, pd1d, md1d = self._calculate_adx(h1d, l1d, c1d, 14)
                        atr1d = self._calculate_atr(h1d, l1d, c1d, 14)
                        e9_1d = self._ema(c1d, 9)[-1] if len(c1d) >= 9 else c1d[-1]
                        e21_1d = self._ema(c1d, 21)[-1] if len(c1d) >= 21 else c1d[-1]
                        e50_1d = self._ema(c1d, 50)[-1] if len(c1d) >= 50 else c1d[-1]
                        indicators[symbol]["adx_1d"] = round(a1d, 2)
                        indicators[symbol]["plus_di_1d"] = round(pd1d, 2)
                        indicators[symbol]["minus_di_1d"] = round(md1d, 2)
                        indicators[symbol]["atr_1d"] = round(atr1d, 4)
                        indicators[symbol]["ema_9_1d"] = round(float(e9_1d), 6)
                        indicators[symbol]["ema_21_1d"] = round(float(e21_1d), 6)
                        indicators[symbol]["ema_50_1d"] = round(float(e50_1d), 6)

                # 1w ADX / ATR / EMA / RSI / MACD — 供长期慢周期确认使用
                key_1w = (symbol, "1w")
                if key_1w in klines:
                    df1w = klines[key_1w]
                    c1w = df1w['close'].values.astype(float) if 'close' in df1w.columns else None
                    h1w = df1w['high'].values.astype(float) if 'high' in df1w.columns else None
                    l1w = df1w['low'].values.astype(float) if 'low' in df1w.columns else None
                    if c1w is not None and h1w is not None and l1w is not None and len(c1w) >= 20:
                        a1w, pd1w, md1w = self._calculate_adx(h1w, l1w, c1w, 14)
                        atr1w = self._calculate_atr(h1w, l1w, c1w, 14)
                        e9_1w = self._ema(c1w, 9)[-1] if len(c1w) >= 9 else c1w[-1]
                        e21_1w = self._ema(c1w, 21)[-1] if len(c1w) >= 21 else c1w[-1]
                        e50_1w = self._ema(c1w, 50)[-1] if len(c1w) >= 50 else c1w[-1]
                        indicators[symbol]["adx_1w"] = round(a1w, 2)
                        indicators[symbol]["plus_di_1w"] = round(pd1w, 2)
                        indicators[symbol]["minus_di_1w"] = round(md1w, 2)
                        indicators[symbol]["atr_1w"] = round(atr1w, 4)
                        indicators[symbol]["ema_9_1w"] = round(float(e9_1w), 6)
                        indicators[symbol]["ema_21_1w"] = round(float(e21_1w), 6)
                        indicators[symbol]["ema_50_1w"] = round(float(e50_1w), 6)
                        try:
                            r1w = self._calculate_rsi(c1w, 14)
                            m1w, _ms1w, mh1w = self._calculate_macd(c1w)
                            indicators[symbol]["rsi_1w"] = round(float(r1w), 2)
                            indicators[symbol]["macd_1w"] = round(float(m1w), 6)
                            indicators[symbol]["macd_hist_1w"] = round(float(mh1w), 6)
                        except Exception:
                            pass

                # Fix 17b/17c: 链上/宏观/期权数据注入（总控长线分析需要 fear_greed/options_skew 等）
                # 原只算K线技术指标 → 链上/宏观/期权维度对总控不可见
                try:
                    from services.onchain_data_collector import onchain_collector as _oc_udp
                    _oc_d = _oc_udp.collect_all([symbol]).get(symbol, {})
                    if isinstance(_oc_d, dict):
                        for _k in ('fear_greed','active_addresses','exchange_net_flow',
                                   'tvl','btc_dominance','whale_tx_count'):
                            _v = _oc_d.get(_k)
                            if _v is not None and _v != 0:
                                indicators[symbol][_k] = float(_v)
                except Exception:
                    pass
                try:
                    from backend.services.options_data_collector import get_options_for_symbol as _gof_udp
                    _opt_d = _gof_udp(symbol)
                    if _opt_d:
                        for _k in ('options_skew','put_call_ratio','iv_term_structure'):
                            _v = _opt_d.get(_k)
                            if _v is not None:
                                indicators[symbol][_k] = float(_v)
                except Exception:
                    pass

                # 短周期指标 (5m / 15m) — 供短线分析独立使用
                for _stf in ("5m", "15m"):
                    short_key = (symbol, _stf)
                    if short_key not in klines or short_key == kline_key:
                        continue
                    sdf = klines[short_key]
                    sc = sdf['close'].values.astype(float) if 'close' in sdf.columns else None
                    sh = sdf['high'].values.astype(float) if 'high' in sdf.columns else None
                    sl = sdf['low'].values.astype(float) if 'low' in sdf.columns else None
                    if sc is not None and len(sc) >= 20:
                        s_rsi = self._calculate_rsi(sc, 14)
                        s_macd, s_sig, s_hist = self._calculate_macd(sc)
                        s_ema9 = self._ema(sc, 9)[-1] if len(sc) >= 9 else sc[-1]
                        s_ema21 = self._ema(sc, 21)[-1] if len(sc) >= 21 else sc[-1]
                        indicators[symbol]["short_rsi"] = round(s_rsi, 2)
                        indicators[symbol]["short_macd"] = round(s_macd, 6)
                        indicators[symbol]["short_macd_hist"] = round(s_hist, 6)
                        indicators[symbol]["short_ema_trend"] = round(
                            (s_ema9 - s_ema21) / s_ema21 if s_ema21 != 0 else 0, 6
                        )

                # =============================================
                # P0-高阶衍生特征 (12个) — K线形态 + 量价关系 + 波动结构
                # =============================================
                _last_idx = len(close) - 1
                _o = float(open_vals[_last_idx]) if open_vals is not None else float(close[_last_idx])
                _c = float(close[_last_idx])
                _h = float(high[_last_idx]) if high is not None else _c
                _l = float(low[_last_idx]) if low is not None else _c
                _hl_range = _h - _l if _h != _l else 1e-8

                # F1: body_ratio — K线实体占比
                _body = abs(_c - _o)
                indicators[symbol]["body_ratio"] = round(_body / _hl_range, 4) if _hl_range > 0 else 0.0

                # F2: upper_shadow_ratio — 上影线占比
                indicators[symbol]["upper_shadow_ratio"] = round(
                    (_h - max(_o, _c)) / _hl_range, 4) if _hl_range > 0 else 0.0

                # F3: lower_shadow_ratio — 下影线占比
                indicators[symbol]["lower_shadow_ratio"] = round(
                    (min(_o, _c) - _l) / _hl_range, 4) if _hl_range > 0 else 0.0

                # F4: doji_score — 十字星得分 (>0.9=十字星)
                indicators[symbol]["doji_score"] = round(1.0 - indicators[symbol]["body_ratio"], 4)

                # F5: volume_price_corr — 20周期量价相关性
                if volume is not None and len(volume) >= 20:
                    _v20 = volume[-20:]
                    _c20 = close[-20:]
                    _v_std = np.std(_v20)
                    _c_std = np.std(_c20)
                    if _v_std > 0 and _c_std > 0:
                        indicators[symbol]["volume_price_corr"] = round(
                            float(np.corrcoef(_v20, _c20)[0, 1]), 4)
                    else:
                        indicators[symbol]["volume_price_corr"] = 0.0
                else:
                    indicators[symbol]["volume_price_corr"] = 0.0

                # F6: volatility_skew — 波动偏度 (上行波动 vs 下行波动)
                if len(close) >= 10:
                    _ups = []
                    _downs = []
                    for _i in range(max(0, len(close) - 10), len(close)):
                        if high is not None and low is not None:
                            _up_move = float(high[_i]) - float(close[_i])
                            _down_move = float(close[_i]) - float(low[_i])
                            _ups.append(max(_up_move, 0))
                            _downs.append(max(_down_move, 0))
                    _avg_up = np.mean(_ups) if _ups else 0
                    _avg_down = np.mean(_downs) if _downs else 0
                    indicators[symbol]["volatility_skew"] = round(
                        (_avg_up - _avg_down) / max(_avg_up + _avg_down, 1e-8), 4)
                else:
                    indicators[symbol]["volatility_skew"] = 0.0

                # F7: trend_efficiency — 趋势效率 (净位移/总路径)
                if len(close) >= 20:
                    _net_move = abs(float(close[-1]) - float(close[-20]))
                    _total_path = float(sum(abs(np.diff(close[-20:]))))
                    indicators[symbol]["trend_efficiency"] = round(
                        _net_move / max(_total_path, 1e-8), 4)
                else:
                    indicators[symbol]["trend_efficiency"] = 0.0

                # F8: volume_climax — 放量倍率 (当前量 / 20周期均量)
                if volume is not None and len(volume) >= 21:
                    _v_now = float(volume[-1])
                    _v_sma20 = float(np.mean(volume[-21:-1]))
                    indicators[symbol]["volume_climax"] = round(
                        _v_now / max(_v_sma20, 1e-8), 4)
                else:
                    indicators[symbol]["volume_climax"] = 1.0

                # F9: price_acceleration — 价格加速度 ROC(5) - ROC(20)
                if len(close) >= 21:
                    _roc5 = (float(close[-1]) - float(close[-5])) / max(abs(float(close[-5])), 1e-8)
                    _roc20 = (float(close[-1]) - float(close[-20])) / max(abs(float(close[-20])), 1e-8)
                    indicators[symbol]["price_acceleration"] = round(_roc5 - _roc20, 6)
                else:
                    indicators[symbol]["price_acceleration"] = 0.0

                # F10: ema_ribbon_width — EMA带宽度 (EMA9-EMA50)/close
                _e9 = indicators[symbol].get("ema_9", float(close[-1]))
                _e50 = indicators[symbol].get("ema_50", float(close[-1]))
                indicators[symbol]["ema_ribbon_width"] = round(
                    (_e9 - _e50) / max(abs(float(close[-1])), 1e-8), 6)

                # F11: rsi_divergence — RSI与价格背离 (RSI斜率 vs 价格斜率)
                if len(close) >= 10:
                    _c10 = close[-10:]
                    _c_slope = float(np.polyfit(range(10), _c10.astype(float), 1)[0]) if len(_c10) >= 5 else 0
                    # 简易RSI斜率: 用最后5个RSI值
                    _rsi_slope = 0.0
                    try:
                        _rsi_vals = []
                        for _j in range(max(0, len(close) - 15), len(close) - 4):
                            _seg = close[_j:_j + 14]
                            if len(_seg) >= 14:
                                _rsi_vals.append(self._calculate_rsi(_seg, 14))
                        if len(_rsi_vals) >= 5:
                            _rsi_slope = float(np.polyfit(range(len(_rsi_vals)), _rsi_vals, 1)[0])
                    except Exception:
                        _rsi_slope = 0.0
                    indicators[symbol]["rsi_divergence"] = round(
                        1.0 if (_c_slope > 0 and _rsi_slope < 0) or (_c_slope < 0 and _rsi_slope > 0) else 0.0, 2)
                else:
                    indicators[symbol]["rsi_divergence"] = 0.0

                # F12: volume_imbalance — 买卖失衡度 (基于K线自身)
                if volume is not None and len(volume) >= 10:
                    _buy_vol = 0.0
                    _sell_vol = 0.0
                    for _i in range(max(0, len(volume) - 10), len(volume)):
                        _vo = float(open_vals[_i]) if open_vals is not None else 0
                        _vc = float(close[_i])
                        _vv = float(volume[_i])
                        if _vc >= _vo:
                            _buy_vol += _vv
                        else:
                            _sell_vol += _vv
                    _total_v = _buy_vol + _sell_vol
                    indicators[symbol]["volume_imbalance"] = round(
                        (_buy_vol - _sell_vol) / max(_total_v, 1e-8), 4)
                else:
                    indicators[symbol]["volume_imbalance"] = 0.0
            except Exception as e:
                logger.debug(f"计算 {symbol} 技术指标失败: {e}")
        
        return indicators
    
    # ========== 辅助计算方法 ==========
    
    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """指数移动平均"""
        if len(data) < period:
            return data
        alpha = 2 / (period + 1)
        ema = np.zeros(len(data))
        ema[period-1] = np.mean(data[:period])
        for i in range(period, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i-1]
        return ema
    
    def _calculate_rsi(self, close: np.ndarray, period: int = 14) -> float:
        """计算RSI"""
        if len(close) < period + 1:
            # [2026-07-10] 数据不足返回 NaN 而非 50.0（中性占位，无法区分真假）
            return float('nan')

        delta = np.diff(close)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)

        avg_gain = np.mean(gain[-period:])
        avg_loss = np.mean(loss[-period:])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return float(100 - (100 / (1 + rs)))

    def _calculate_macd(
        self,
        close: np.ndarray,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9
    ) -> Tuple[float, float, float]:
        """计算MACD"""
        if len(close) < slow:
            # [2026-07-10] 数据不足返回 NaN 而非 0.0
            return float('nan'), float('nan'), float('nan')

        ema_fast = self._ema(close, fast)
        ema_slow = self._ema(close, slow)
        macd_line = ema_fast - ema_slow

        if len(macd_line) < signal:
            return float(macd_line[-1]), float('nan'), float(macd_line[-1])

        signal_line = self._ema(macd_line, signal)
        histogram = macd_line[-1] - signal_line[-1]

        return float(macd_line[-1]), float(signal_line[-1]), float(histogram)

    def _calculate_atr(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = 14
    ) -> float:
        """计算ATR"""
        if len(close) < period:
            # [2026-07-10] 数据不足返回 NaN 而非 0.0。ATR=0 会让止损价=入场价秒止损。
            return float('nan')

        tr = np.maximum(
            high[1:] - low[1:],
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        )

        return float(np.mean(tr[-period:]))

    def _calculate_adx(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = 14
    ) -> Tuple[float, float, float]:
        """计算 ADX / +DI / -DI（趋势强度与方向）"""
        n = len(close)
        if n < period + 1:
            # [2026-07-10] 数据不足返回 NaN 而非 20.0/50.0（占位会让 plus_di==minus_di 必判 neutral）
            return float('nan'), float('nan'), float('nan')

        up_move = high[1:] - high[:-1]
        down_move = low[:-1] - low[1:]

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        tr = np.maximum(
            high[1:] - low[1:],
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        )

        def _smooth(arr, p):
            out = np.zeros(len(arr))
            out[p - 1] = np.sum(arr[:p])
            for i in range(p, len(arr)):
                out[i] = out[i - 1] - out[i - 1] / p + arr[i]
            return out

        atr_s = _smooth(tr, period)
        plus_dm_s = _smooth(plus_dm, period)
        minus_dm_s = _smooth(minus_dm, period)

        # +DI / -DI
        with np.errstate(divide='ignore', invalid='ignore'):
            plus_di = np.where(atr_s > 0, 100.0 * plus_dm_s / atr_s, 0.0)
            minus_di = np.where(atr_s > 0, 100.0 * minus_dm_s / atr_s, 0.0)

        di_sum = plus_di + minus_di
        with np.errstate(divide='ignore', invalid='ignore'):
            dx = np.where(di_sum > 0, 100.0 * np.abs(plus_di - minus_di) / di_sum, 0.0)

        # ADX = smoothed DX
        adx_arr = _smooth(dx, period)
        with np.errstate(divide='ignore', invalid='ignore'):
            adx_arr = np.where(adx_arr > 0, adx_arr / period, 0.0)

        adx_val = float(adx_arr[-1]) if len(adx_arr) > 0 else 20.0
        pdi_val = float(plus_di[-1]) if len(plus_di) > 0 else 50.0
        mdi_val = float(minus_di[-1]) if len(minus_di) > 0 else 50.0

        return adx_val, pdi_val, mdi_val
    
    # ========== 便捷方法 ==========
    
    def get_market_price(self, symbol: str) -> float:
        """获取市场价格（从当前快照）"""
        snapshot = self.get_snapshot()
        if snapshot and symbol in snapshot.markets:
            return snapshot.markets[symbol].price
        return 0.0

    def klines_for_coordinator(
        self,
        symbol: str,
        snapshot: Optional["UnifiedSnapshot"] = None,
        periods: Tuple[str, ...] = ("15m", "1h", "4h", "1d"),
        min_bars: int = 20,
    ) -> Dict[str, list]:
        """把已采集快照里的 K 线转换成 StrategyCoordinator 消费的形态。

        2026-07-06 整改（unified_data_pool 全量整合 · 灰度切片）：
        `strategy_coordinator._load_env_klines` 需要的是
        `{period: [kline_dict, ...]}`（每周期 ≥ min_bars 根）；而快照里
        `UnifiedSnapshot.klines` 是 `Dict[(symbol, tf) -> DataFrame]`。本方法做
        纯转换（DataFrame → records list），**不触发任何拉取、无副作用**：
        - 快照缺失 / 该周期不存在 / 不足 min_bars → 该周期直接跳过（由消费端回退实时拉取兜底）；
        - 返回空 dict 表示"无可复用快照 K 线"，调用方行为与不传快照完全一致（向后兼容）。

        这是"决策主链与统一快照共用同一份时点 K 线"的生产端接线所需的唯一转换器，
        本身零风险；是否真正喂给 coordinator 由主链的灰度开关控制。
        """
        snap = snapshot if snapshot is not None else self.get_snapshot()
        if snap is None:
            return {}
        sym = str(symbol).upper()
        out: Dict[str, list] = {}
        for tf in periods:
            df = snap.klines.get((sym, tf))
            if df is None:
                continue
            try:
                if len(df) < min_bars:
                    continue
                records = df.to_dict("records")
            except Exception as err:
                logger.debug("[UnifiedDataPool] klines_for_coordinator %s/%s 转换失败: %s", sym, tf, err)
                continue
            if len(records) >= min_bars:
                out[tf] = records
        return out
    
    def get_strategy_context(self) -> Dict[str, Any]:
        """获取策略上下文（用于AI提示词）"""
        snapshot = self.get_snapshot()
        if not snapshot:
            return {}
        
        s = snapshot.strategy
        return {
            # 中长期规划
            "market_cycle": s.market_cycle,
            "cycle_confidence": f"{s.cycle_confidence:.1%}",
            "position_bias": s.position_bias,
            "recommended_leverage": s.recommended_leverage,
            "max_position_size": f"{s.max_position_size:.0%}",
            "max_daily_loss": f"{s.max_daily_loss_pct:.0%}",
            "key_support": f"${s.key_support:,.2f}" if s.key_support else "N/A",
            "key_resistance": f"${s.key_resistance:,.2f}" if s.key_resistance else "N/A",
            "regime_warning": "⚠️ 周期转换预警" if s.regime_warning else "",
            
            # 短期战术
            "tactical_action": s.tactical_action,
            "tactical_confidence": f"{s.tactical_confidence:.1%}",
            "entry_timing": s.entry_timing,
            "market_condition": s.market_condition,
            "suggested_stop_loss": f"${s.suggested_stop_loss:,.2f}" if s.suggested_stop_loss else "N/A",
            "suggested_take_profit": f"${s.suggested_take_profit:,.2f}" if s.suggested_take_profit else "N/A",
        }
    
    def get_factors_summary(self, symbol: str) -> str:
        """获取因子摘要（用于AI提示词）"""
        snapshot = self.get_snapshot()
        if not snapshot or symbol not in snapshot.indicators:
            return "无因子数据"
        
        factors = snapshot.indicators[symbol]
        lines = []
        
        # 关键因子
        key_factors = ["rsi", "macd", "atr", "bb_width", "ema_trend", "funding_rate"]
        for name in key_factors:
            if name in factors:
                lines.append(f"- {name}: {factors[name]:.4f}")
        
        return "\n".join(lines) if lines else "无因子数据"

    # ========== 智能多周期数据采集 ==========

    def _capture_news_by_symbol(self, symbols: List[str]) -> Dict[str, List[Dict]]:
        """每个 symbol 独立采集新闻（修复：此前仅 symbols[0]）。"""
        if not symbols:
            return {}
        try:
            from backend.services.news_intelligence_service import news_intelligence

            def _fetch(sym: str):
                try:
                    return sym, news_intelligence.get_recent_signals(sym, hours=4)
                except Exception as e:
                    logger.debug(f"[UnifiedDataPool] {sym} 新闻失败: {e}")
                    return sym, []

            result: Dict[str, List[Dict]] = {}
            futures = [_parallel_capture_pool.submit(_fetch, s) for s in symbols]
            for f in as_completed(futures, timeout=25):
                try:
                    sym, rows = f.result(timeout=5)
                    result[sym] = rows or []
                except Exception:
                    pass
            for s in symbols:
                result.setdefault(s, [])
            return result
        except Exception as e:
            logger.debug(f"[UnifiedDataPool] 新闻并行采集失败: {e}")
            return {s: [] for s in symbols}

    def _capture_intelligence_prompts(self, symbols: List[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        timeout_seconds = _env_int("UNIFIED_DATA_POOL_INTELLIGENCE_TIMEOUT", 45, min_value=5, max_value=180)
        try:
            from backend.services.intelligence_signal_engine import intelligence_signal_engine

            def _one(sym: str):
                try:
                    sig = intelligence_signal_engine.compute_trading_signal(sym)
                    return sym, sig.to_prompt_text()
                except Exception:
                    return sym, ""

            futures = [_parallel_capture_pool.submit(_one, s) for s in symbols]
            try:
                for f in as_completed(futures, timeout=timeout_seconds):
                    try:
                        sym, txt = f.result(timeout=8)
                        if txt:
                            out[sym] = txt
                    except Exception:
                        pass
            except FuturesTimeoutError:
                logger.warning(
                    "[UnifiedDataPool] 情报 prompt 采集超时: %d/%d 完成, timeout=%ss",
                    len(out), len(symbols), timeout_seconds,
                )
        except Exception as e:
            logger.debug(f"[UnifiedDataPool] 情报 prompt 采集失败: {e}")
        for sym in symbols:
            out.setdefault(sym, "=== INTELLIGENCE TRADING SIGNAL ===\nstatus: degraded_unavailable")
        return out

    def _enrich_indicators_from_context(
        self, snapshot: "UnifiedSnapshot", symbols: List[str],
    ) -> None:
        """把市场/衍生品/情绪写入 indicators，编排器与 LLM 可读同一套数。"""
        for sym in symbols:
            ind = snapshot.indicators.setdefault(sym, {})
            mkt = snapshot.markets.get(sym)
            if mkt:
                if mkt.price:
                    ind["close"] = ind.get("close") or mkt.price
                ind["funding_rate"] = mkt.funding_rate
                ind["open_interest"] = mkt.open_interest
                if mkt.price_24h_change is not None:
                    # mkt.price_24h_change 来自 market_data_hub 的 (mark-prev)/prev*100，
                    # 是百分数形式（0.23 表示 0.23%）。indicators 里的 price_change_24h
                    # 按计算路径（_pc_24h=(close[-1]-close[-25])/close[-25]）约定为
                    # 「小数比例」（0.0023 表示 0.23%）。
                    # 原代码用 abs(x)>1 启发式决定是否 /100，对小数值（如 0.23% 的真实
                    # 变动 = 0.23）会误判为已是小数而不除 100，再经下游 *100 后被放大成
                    # 23%，导致 regime_agent 误判为 extreme、短线因子开仓被 Gate 全部 block。
                    ind["price_change_24h"] = (mkt.price_24h_change or 0) / 100.0
            deriv = snapshot.derivatives_snapshot.get(sym, {})
            if deriv:
                ind["oi_total"] = deriv.get("oi_total", 0)
                ind["oi_change_1h"] = deriv.get("oi_change_1h", 0)
                ind["liquidation_1h_long"] = deriv.get("liquidation_1h_long", 0)
                ind["liquidation_1h_short"] = deriv.get("liquidation_1h_short", 0)
                ind["long_short_ratio"] = deriv.get("long_short_ratio", 1.0)
                ind["derivatives_signal"] = deriv.get("signal", "neutral")
            sent = snapshot.sentiment_index.get(sym, {})
            if sent:
                ind["sentiment_index"] = sent.get("index", 50)
            whale = snapshot.whale_signals.get(sym, {})
            if whale:
                ind["whale_direction"] = whale.get("direction", 0)

    def audit_snapshot_completeness(
        self, snapshot: "UnifiedSnapshot", symbols: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """审计快照完整性 — 缺失项写入 data_completeness 供健康检查告警。

        对新增交易对（无 1d K 线或 1d 不足 7 根）放宽审计要求，标记为 degraded。
        """
        required_tfs = {"5m", "15m", "1h", "4h", "1d"}
        report: Dict[str, Dict[str, Any]] = {}
        for sym in symbols:
            missing = []
            kline_tfs = {tf for (s, tf) in (snapshot.klines or {}) if s == sym}

            # 检测新增交易对：无 1d K 线或数据过少
            kline_1d = snapshot.klines.get((sym, "1d"))
            is_new_symbol = False
            if "1d" not in kline_tfs:
                is_new_symbol = True
            elif kline_1d is not None and hasattr(kline_1d, '__len__') and len(kline_1d) < 7:
                is_new_symbol = True

            if is_new_symbol:
                # 新增交易对：仅要求有至少一个 K 线周期
                if not kline_tfs:
                    missing.append("kline_any")
            else:
                # 成熟交易对：要求全部 5 个 K 线周期
                for tf in required_tfs:
                    if tf not in kline_tfs:
                        missing.append(f"kline_{tf}")

            ind = snapshot.indicators.get(sym, {})
            try:
                from backend.services.data_readiness_gate import indicators_are_real
                if not indicators_are_real(ind):
                    missing.append("indicators")
            except Exception:
                if not ind:
                    missing.append("indicators")

            # 以下检查对新增交易对跳过（历史数据不足属正常）
            if not is_new_symbol:
                if not snapshot.derivatives_snapshot.get(sym):
                    missing.append("derivatives")
                if not snapshot.per_symbol_planning.get(sym) and not snapshot.strategy.market_cycle:
                    missing.append("long_planning")
                if sym not in snapshot.intelligence_by_symbol:
                    missing.append("intelligence")
                if not ind.get("flow_data_ok"):
                    missing.append("order_flow_cvd_taker")
                k1h = snapshot.klines.get((sym, "1h"))
                if k1h is not None and hasattr(k1h, "columns"):
                    if "flow_data_ok" in k1h.columns and not bool(k1h["flow_data_ok"].any()):
                        if "order_flow_cvd_taker" not in missing:
                            missing.append("order_flow_kline_series")

            # price 是所有交易对的核心要求，不豁免
            mkt = snapshot.markets.get(sym)
            if not mkt or not mkt.price:
                missing.append("price")
            report[sym] = {
                "ok": len(missing) == 0,
                "missing": missing,
                "kline_periods": sorted(kline_tfs),
                "degraded": is_new_symbol,
            }
        return report

    def merge_snapshot_into_market_summary(
        self,
        market_summary: Dict[str, Any],
        snapshot: "UnifiedSnapshot",
        symbols: Optional[List[str]] = None,
    ) -> None:
        """将统一快照写入 market_summary，保证编排器评估与 LLM 决策同源。"""
        if not snapshot or not market_summary:
            return
        syms = symbols or list(market_summary.keys())
        for sym in syms:
            entry = market_summary.setdefault(sym, {})
            if not isinstance(entry, dict):
                continue
            mkt = snapshot.markets.get(sym)
            if mkt and mkt.price:
                entry["current_price"] = mkt.price
                entry.pop("error", None)
                if entry.get("data_source") in ("cache_miss", "pending", None):
                    entry["data_source"] = "unified_snapshot"
                entry["data_reliable"] = True
                # 修复1：不再无条件标True——检查K线/指标是否缺失
                _comp = (snapshot.data_completeness or {}).get(sym, {})
                _missing = set(_comp.get("missing") or [])
                if "klines" in _missing or "indicators" in _missing:
                    entry["data_reliable"] = False
                    entry["data_stale_reason"] = "klines_or_indicators_missing"
            ind = snapshot.indicators.get(sym, {})
            if ind:
                entry["atr_value"] = ind.get("atr", entry.get("atr_value", 0))
                entry["funding_rate"] = ind.get("funding_rate", entry.get("funding_rate", 0))
                entry["price_change_1h_pct"] = (ind.get("price_change_1h", 0) or 0) * 100
                entry["price_change_24h_pct"] = (ind.get("price_change_24h", 0) or 0) * 100
            deriv = snapshot.derivatives_snapshot.get(sym, {})
            if deriv:
                # 修复2：衍生品降级占位标记穿透——不让AI把占位neutral当真实信号
                if deriv.get("data_quality") == "degraded":
                    entry["derivatives_degraded"] = True
                    entry["derivatives_signal"] = "⚠️数据不可用"
                else:
                    entry["derivatives_signal"] = deriv.get("signal", entry.get("derivatives_signal", "neutral"))
                # [2026-08-15 消费端验收] degraded 时数值字段不再写占位 0/1.0：
                # 原 oi_total=0 / long_short_ratio=1.0 / liquidation=0 以真实形态
                # 流入市场摘要。现改为 None（缺数据），下游据 None 诚实降级。
                _degraded = deriv.get("data_quality") == "degraded"
                entry["oi_total"] = None if _degraded else deriv.get("oi_total", 0)
                entry["oi_change_1h"] = deriv.get("oi_change_1h", 0)
                entry["liquidation_1h_long"] = None if _degraded else deriv.get("liquidation_1h_long", 0)
                entry["liquidation_1h_short"] = None if _degraded else deriv.get("liquidation_1h_short", 0)
                entry["long_short_ratio"] = None if _degraded else deriv.get("long_short_ratio", 1.0)
                entry["derivatives_interpretation"] = deriv.get("interpretation", "")
            sent = snapshot.sentiment_index.get(sym, {})
            if sent:
                entry["sentiment_index"] = sent.get("index", entry.get("sentiment_index", 50))
                entry["sentiment_zone"] = sent.get("zone", entry.get("sentiment_zone", "neutral"))
            whale = snapshot.whale_signals.get(sym, {})
            if whale:
                entry["whale_direction"] = whale.get("direction", entry.get("whale_direction", 0))
                entry["whale_summary"] = whale.get("summary", "")
            news = (snapshot.news_by_symbol or {}).get(sym, [])
            if news:
                top = news[0]
                entry["news_top_event"] = top.get("title", entry.get("news_top_event", ""))
                entry["news_impact"] = top.get("strength", 0)
            intel_txt = (snapshot.intelligence_by_symbol or {}).get(sym, "")
            if intel_txt:
                entry["intelligence_prompt"] = intel_txt
            plan = (snapshot.per_symbol_planning or {}).get(sym)
            if plan:
                entry["market_cycle"] = getattr(getattr(plan, "market_cycle", None), "value", None) or str(getattr(plan, "market_cycle", ""))
                entry["position_bias"] = getattr(plan, "position_bias", entry.get("position_bias", "neutral"))
                entry["cycle_confidence"] = float(getattr(plan, "cycle_confidence", 0) or 0)
            comp = (snapshot.data_completeness or {}).get(sym, {})
            entry["data_completeness"] = comp
            entry["data_complete"] = comp.get("ok", True)
            # data_reliable 表示价格/行情源是否可用；不要因情报、衍生品、
            # 订单流等非价格字段缺失，把 bulk_ticker 等真实价格误判为不可靠。
            missing = set(comp.get("missing") or [])
            if "price" in missing:
                entry["data_reliable"] = False

    def _capture_whale_signals(self, symbols: List[str]) -> Dict[str, Any]:
        """并行采集鲸鱼信号，避免多 symbol 串行 API 等待。"""
        if not symbols:
            return {}
        try:
            from backend.services.whale_tracker_service import whale_tracker

            def _fetch_one(sym: str):
                try:
                    sig = whale_tracker.get_whale_signal(sym)
                    return sym, {
                        "direction": sig.direction,
                        "confidence": sig.confidence,
                        "count": sig.activities_count,
                        "total_usd": sig.total_usd,
                        "summary": sig.summary,
                    }
                except Exception as e:
                    logger.debug(f"[UnifiedDataPool] {sym} 鲸鱼信号失败: {e}")
                    return sym, None

            t0 = time.time()
            result: Dict[str, Any] = {}
            futures = [_parallel_capture_pool.submit(_fetch_one, s) for s in symbols]
            for f in as_completed(futures, timeout=30):
                try:
                    sym, data = f.result(timeout=5)
                    if data is not None:
                        result[sym] = data
                except Exception as e:
                    logger.debug(f"[UnifiedDataPool] 鲸鱼 future 失败: {e}")
            logger.debug(
                f"[UnifiedDataPool] 鲸鱼并行采集: {len(result)}/{len(symbols)}, "
                f"耗时={(time.time()-t0)*1000:.0f}ms"
            )
            return result
        except Exception as e:
            logger.debug(f"[UnifiedDataPool] 鲸鱼信号采集失败: {e}")
            return {}

    def _capture_derivatives(self, symbols: List[str]) -> Dict[str, Any]:
        """并行采集衍生品快照（Binance/Coinalyze）。

        每个 symbol 走独立线程拉取外部 API，总耗时 ≈ max(单 symbol 耗时)，
        而不是 sum(所有 symbol 耗时)。7 个 symbol 的采集可从 ~100s 降到 ~15s。
        """
        if not symbols:
            return {}
        timeout_seconds = _env_int("UNIFIED_DATA_POOL_DERIVATIVES_TIMEOUT", 60, min_value=10, max_value=180)

        def _neutral_derivatives(sym: str, reason: str) -> Dict[str, Any]:
            return {
                "funding_rate": 0.0,
                "oi_total": 0.0,
                "oi_change_1h": 0.0,
                "liquidation_1h_long": 0.0,
                "liquidation_1h_short": 0.0,
                "long_short_ratio": 1.0,
                "signal": "neutral",
                "signal_strength": 0.0,
                "interpretation": f"衍生品数据暂不可用，已降级为中性占位: {reason}",
                "data_quality": "degraded",
                "symbol": sym,
            }

        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics

            def _fetch_one(sym: str):
                try:
                    # Fix 17a: 优先用 Market DB 的 get_indicator_value（本地可靠，不依赖外部API）
                    # 原完全依赖 derivatives_analytics_service → API 超时时返回中性占位 → 总控瞎判断
                    try:
                        from backend.services.market_flow_indicators import get_indicator_value as _giv
                        _oi_d = _giv(None, sym, "OI_DELTA", "1h")
                        _cvd = _giv(None, sym, "CVD", "1h")
                        _taker = _giv(None, sym, "TAKER", "1h")
                        _imb = _giv(None, sym, "IMBALANCE", "1h")
                        if _oi_d is not None and _taker is not None:
                            # Market DB 有数据 → 构建衍生品快照
                            _signal = "bullish" if (_taker > 1.0 and _oi_d > 0) else (
                                "bearish" if (_taker < 0.5 or (_oi_d < -2 and _cvd is not None and _cvd < 0)) else "neutral"
                            )
                            _strength = min(1.0, abs(_oi_d) / 5.0 + abs(_taker - 1.0))
                            # [2026-08-15 消费端验收] 原 funding_rate/oi_total/liquidation
                            # 在此硬编码 0.0 且 data_quality="market_db"（非 degraded）——
                            # 假 0 以「真实数据」身份流入 market_summary。现改为：
                            # funding ← data_center 落库（perp_funding 恒可用）；
                            # OI 绝对值 ← market_asset_metrics 真实值；
                            # 取不到时显式 None（下游据 None 诚实降级），绝不写 0。
                            _fr = None
                            _abs_oi = None
                            try:
                                from backend.services.data_center import data_center as _dc
                                _deriv = _dc.get_derivatives(sym) or {}
                                _fr = _deriv.get("funding_rate")
                                _abs_oi = _deriv.get("open_interest")
                            except Exception:
                                pass
                            if _abs_oi is None:
                                try:
                                    from backend.services.factor_engine.factor_bridge import fetch_real_oi_pair
                                    _cur_oi, _prev_oi = fetch_real_oi_pair(sym)
                                    _abs_oi = _cur_oi
                                except Exception:
                                    pass
                            return sym, {
                                "funding_rate": float(_fr) if _fr is not None else None,
                                "oi_total": float(_abs_oi) if _abs_oi is not None else None,
                                "oi_change_1h": float(_oi_d),
                                "liquidation_1h_long": None,
                                "liquidation_1h_short": None,
                                "long_short_ratio": float(_taker) if _taker else None,
                                "signal": _signal,
                                "signal_strength": round(_strength, 3),
                                "interpretation": f"MarketDB: OI变化={_oi_d:.2f}% CVD={_cvd or 0:.0f} Taker={_taker:.2f} Imb={_imb or 0:.2f}",
                                "data_quality": "market_db",
                                "cvd": float(_cvd) if _cvd else 0.0,
                                "imbalance": float(_imb) if _imb else 0.0,
                            }
                    except Exception:
                        pass

                    # Market DB 无数据 → 回退到 derivatives_analytics_service（外部API）
                    snap = derivatives_analytics.get_snapshot(sym)
                    return sym, {
                        "funding_rate": snap.funding_rate,
                        "oi_total": snap.oi_total,
                        "oi_change_1h": snap.oi_change_1h,
                        "liquidation_1h_long": snap.liquidation_1h_long,
                        "liquidation_1h_short": snap.liquidation_1h_short,
                        "long_short_ratio": snap.long_short_ratio,
                        "signal": snap.signal,
                        "signal_strength": snap.signal_strength,
                        "interpretation": snap.interpretation,
                    }
                except Exception as e:
                    logger.debug(f"[UnifiedDataPool] {sym} 衍生品失败: {e}")
                    return sym, None

            t0 = time.time()
            result: Dict[str, Any] = {}
            futures = [_parallel_capture_pool.submit(_fetch_one, s) for s in symbols]
            try:
                for f in as_completed(futures, timeout=timeout_seconds):
                    try:
                        sym, data = f.result(timeout=8)
                        if data is not None:
                            result[sym] = data
                    except Exception as e:
                        logger.debug(f"[UnifiedDataPool] 衍生品 future 失败: {e}")
            except FuturesTimeoutError:
                logger.warning(
                    "[UnifiedDataPool] 衍生品并行采集超时: %d/%d 完成, timeout=%ss",
                    len(result), len(symbols), timeout_seconds,
                )
            for sym in symbols:
                result.setdefault(sym, _neutral_derivatives(sym, "timeout_or_source_unavailable"))
            logger.info(
                f"[UnifiedDataPool] 衍生品并行采集: {len(result)}/{len(symbols)}, "
                f"耗时={(time.time()-t0)*1000:.0f}ms"
            )
            return result
        except Exception as e:
            logger.debug(f"[UnifiedDataPool] 合约数据采集失败: {e}")
            return {sym: _neutral_derivatives(sym, f"{type(e).__name__}: {e}") for sym in symbols}

    def _capture_sentiment(self, symbols: List[str]) -> Dict[str, Any]:
        try:
            from backend.services.sentiment_composite_service import sentiment_composite
            result = {}
            for s in symbols:
                r = sentiment_composite.calculate(s)
                result[s] = {
                    "index": r.index,
                    "zone": r.zone,
                    "factors": r.factors,
                    "guidance": r.trading_guidance,
                }
            return result
        except Exception as e:
            logger.debug(f"[UnifiedDataPool] 情绪指数采集失败: {e}")
            return {}

    # [2026-07-10 Phase1] 全市场聚合数据采集
    def _capture_aggregate_orderbook(self, symbols: List[str]) -> Dict[str, Any]:
        """多所聚合订单簿（买卖失衡 + 跨所价差）。"""
        try:
            from backend.services.market_aggregation.aggregate_orderbook_collector import (
                aggregate_orderbook_collector,
            )
            return aggregate_orderbook_collector.collect(symbols)
        except Exception as e:
            logger.debug(f"[UnifiedDataPool] 聚合盘口采集失败: {e}")
            return {}

    def _capture_aggregate_market(self, symbols: List[str]) -> Dict[str, Any]:
        """全市场 OI/费率聚合。"""
        try:
            from backend.services.market_aggregation.aggregate_market_collector import (
                aggregate_market_collector,
            )
            return aggregate_market_collector.collect(symbols)
        except Exception as e:
            logger.debug(f"[UnifiedDataPool] 聚合OI/费率采集失败: {e}")
            return {}

    def get_intelligence_summary(self, symbol: str = "BTC") -> Dict[str, Any]:
        """获取情报摘要（供AI决策使用）"""
        snapshot = self.get_snapshot(max_age=120)
        if not snapshot:
            snapshot = self.capture_snapshot(
                [symbol], include_klines=True, include_strategy=True,
            )
        if not snapshot:
            return {}

        news_summary = ""
        for n in snapshot.news_signals[:3]:
            d = n.get("direction", 0)
            tag = "利多" if d > 0 else ("利空" if d < 0 else "中性")
            news_summary += f"  [{tag}★{n.get('strength', 1)}] {n.get('title', '')[:60]}\n"

        whale_info = snapshot.whale_signals.get(symbol, {})
        deriv_info = snapshot.derivatives_snapshot.get(symbol, {})
        sent_info = snapshot.sentiment_index.get(symbol, {})

        # [2026-08-15 消费端验收] degraded 穿透：衍生品降级时不再把
        # "neutral" 当真实信号返回（原 get("signal","neutral") 让
        # coordinator 把占位中性注入 AI）。degraded → 显式不可用。
        _deriv_signal = deriv_info.get("signal", "neutral")
        if deriv_info.get("data_quality") == "degraded":
            _deriv_signal = "⚠️数据不可用"

        return {
            "news_summary": news_summary or "暂无重大新闻",
            "whale_direction": whale_info.get("direction", 0),
            "whale_summary": whale_info.get("summary", "暂无异动"),
            "derivatives_signal": _deriv_signal,
            "derivatives_interpretation": deriv_info.get("interpretation", ""),
            "sentiment_index": sent_info.get("index", 50),
            "sentiment_zone": sent_info.get("zone", "neutral"),
            "sentiment_guidance": sent_info.get("guidance", ""),
        }

    # ══════════════════════════════════════════════════════
    #  Phase 2: 成交量分布计算（VPVR简化版）
    # ══════════════════════════════════════════════════════

    @staticmethod
    def compute_volume_profile(symbol: str, days: int = 30, bucket_count: int = 20) -> Dict:
        """
        从K线数据中提取成交量分布（VPVR简化版）。
        输出: {\"buckets\": [{\"price_range\": \"$90000-92000\", \"volume_pct\": 35.2, \"label\": \"强支撑\"}, ...]}
        用途: 注入到 Task 2 的关键价位层
        """

        try:
            klines = unified_data_pool.get_kline_series(symbol, interval="1h", limit=24 * days)
            if not klines or len(klines) < 30:
                return {"buckets": [], "insufficient_data": True}

            closes = [float(b.close) for b in klines]
            volumes = [float(b.volume or 0) for b in klines]
            total_vol = sum(volumes)
            if total_vol <= 0:
                return {"buckets": [], "insufficient_data": True}

            cur_price = closes[-1]
            min_price = min(closes)
            max_price = max(closes)
            price_range = max_price - min_price
            if price_range <= 0:
                return {"buckets": [], "insufficient_data": True}

            bucket_size = price_range / bucket_count
            buckets = {}
            for c, v in zip(closes, volumes):
                idx = min(int((c - min_price) / bucket_size), bucket_count - 1)
                lo = min_price + idx * bucket_size
                hi = lo + bucket_size
                key = f"${lo:.0f}-{hi:.0f}"
                buckets[key] = buckets.get(key, 0.0) + v

            result_buckets = []
            for price_key, vol in sorted(buckets.items(), key=lambda x: x[1], reverse=True):
                pct = (vol / total_vol) * 100
                mid = float(price_key.replace("$", "").split("-")[0])
                label = "支撑" if mid < cur_price else ("阻力" if mid > cur_price else "当前价")
                result_buckets.append({
                    "price_range": price_key,
                    "volume_pct": round(pct, 1),
                    "label": label,
                })

            return {
                "buckets": result_buckets[:10],
                "symbol": symbol,
                "days": days,
                "current_price": cur_price,
            }
        except Exception as exc:
            logger.debug(f"[UnifiedDataPool] volume_profile {symbol}: {exc}")
            return {"buckets": [], "error": str(exc)}

    @staticmethod
    def compute_volume_profile_v2(symbol: str, days: int = 30, bucket_count: int = 50, va_pct: float = 0.70) -> Dict:
        """
        VPVR v2 专业版 — 成交量分布图完整分析。

        输出:
        {
            "symbol": str,
            "current_price": float,
            "poc": float,                      # Point of Control (最大成交量价格)
            "poc_volume_pct": float,            # POC成交量占比
            "va": {"low": float, "high": float}, # Value Area (70%成交量区域)
            "vah": float,                       # Value Area High
            "val": float,                       # Value Area Low
            "hvn": [float, ...],                # High Volume Nodes (>1.5x均值)
            "lvn": [float, ...],                # Low Volume Nodes (<0.5x均值)
            "volume_gaps": [{"low": float, "high": float}, ...],  # HVN间LVN区域
            "buckets": [...],                   # 完整bucket列表
            "current_in_va": bool,              # 当前价是否在VA内
            "days": int,
        }

        用途: 精准识别支撑/阻力位，替代简化版 compute_volume_profile()
        """
        import numpy as np

        try:
            klines = unified_data_pool.get_kline_series(symbol, interval="1h", limit=24 * days)
            if not klines or len(klines) < 30:
                return {"buckets": [], "insufficient_data": True}

            # 使用 high/low 范围而非仅收盘价来分配成交量
            closes = [float(b.close) for b in klines]
            highs = [float(b.high) for b in klines]
            lows = [float(b.low) for b in klines]
            volumes = [float(b.volume or 0) for b in klines]
            total_vol = sum(volumes)
            if total_vol <= 0:
                return {"buckets": [], "insufficient_data": True}

            cur_price = closes[-1]
            min_price = min(lows)
            max_price = max(highs)
            price_range = max_price - min_price
            if price_range <= 0:
                return {"buckets": [], "insufficient_data": True}

            bucket_size = price_range / bucket_count
            buckets: Dict[int, float] = {}

            # 按每根K线的高低范围分配成交量到多个bucket
            for h, l, v in zip(highs, lows, volumes):
                if v <= 0:
                    continue
                lo_idx = max(0, min(int((l - min_price) / bucket_size), bucket_count - 1))
                hi_idx = max(0, min(int((h - min_price) / bucket_size), bucket_count - 1))
                if hi_idx == lo_idx:
                    buckets[lo_idx] = buckets.get(lo_idx, 0.0) + v
                else:
                    # 均匀分配成交量到价格范围内的所有bucket
                    span = hi_idx - lo_idx + 1
                    share = v / span
                    for bi in range(lo_idx, hi_idx + 1):
                        buckets[bi] = buckets.get(bi, 0.0) + share

            if not buckets:
                return {"buckets": [], "insufficient_data": True}

            # --- POC (Point of Control) ---
            poc_idx = max(buckets, key=buckets.get)
            poc_vol = buckets[poc_idx]
            poc_price = min_price + (poc_idx + 0.5) * bucket_size

            # --- Value Area (VA) ---
            # 从POC向两侧扩展直到覆盖 va_pct 的成交量
            target_vol = total_vol * va_pct
            accumulated = poc_vol
            left_idx = poc_idx
            right_idx = poc_idx

            while accumulated < target_vol:
                left_vol = buckets.get(left_idx - 1, 0)
                right_vol = buckets.get(right_idx + 1, 0)
                if left_vol >= right_vol and left_idx > min(buckets.keys()):
                    left_idx -= 1
                    accumulated += left_vol
                elif right_idx < max(buckets.keys()):
                    right_idx += 1
                    accumulated += right_vol
                elif left_idx > min(buckets.keys()):
                    left_idx -= 1
                    accumulated += left_vol
                else:
                    break

            val_price = min_price + left_idx * bucket_size
            vah_price = min_price + (right_idx + 1) * bucket_size

            # --- HVN / LVN ---
            bucket_volumes = [buckets.get(i, 0) for i in range(bucket_count)]
            mean_vol = np.mean([v for v in bucket_volumes if v > 0]) if any(v > 0 for v in bucket_volumes) else 0

            hvns = []
            lvns = []
            for i, vol in enumerate(bucket_volumes):
                if vol <= 0:
                    continue
                mid_price = min_price + (i + 0.5) * bucket_size
                if mean_vol > 0 and vol > 1.5 * mean_vol:
                    hvns.append(round(mid_price, 2))
                elif mean_vol > 0 and vol < 0.5 * mean_vol:
                    lvns.append(round(mid_price, 2))

            # --- Volume Gaps (两HVN之间的LVN区域) ---
            volume_gaps = []
            if len(hvns) >= 2:
                sorted_hvns = sorted(hvns)
                for i in range(len(sorted_hvns) - 1):
                    gap_lo = sorted_hvns[i]
                    gap_hi = sorted_hvns[i + 1]
                    # 检查中间是否有LVN
                    has_lvn = any(gap_lo < lvn < gap_hi for lvn in lvns)
                    if has_lvn:
                        volume_gaps.append({"low": gap_lo, "high": gap_hi})

            # --- 构建结果 ---
            result_buckets = []
            for i in sorted(buckets.keys()):
                lo = min_price + i * bucket_size
                hi = lo + bucket_size
                vol = buckets[i]
                pct = (vol / total_vol) * 100
                mid = lo + bucket_size / 2

                # 标签判断
                label_parts = []
                if abs(mid - poc_price) < bucket_size:
                    label_parts.append("POC")
                if any(abs(mid - h) < bucket_size * 0.6 for h in hvns):
                    label_parts.append("HVN")
                if any(abs(mid - l) < bucket_size * 0.6 for l in lvns):
                    label_parts.append("LVN")
                if val_price <= mid <= vah_price:
                    label_parts.append("VA")
                if not label_parts:
                    label_parts.append("支撑" if mid < cur_price else "阻力")

                result_buckets.append({
                    "price_range": f"${lo:.0f}-{hi:.0f}",
                    "mid_price": round(mid, 2),
                    "volume_pct": round(pct, 1),
                    "label": "/".join(label_parts),
                })

            # 按成交量降序排列
            result_buckets.sort(key=lambda x: x["volume_pct"], reverse=True)

            return {
                "buckets": result_buckets[:15],
                "symbol": symbol,
                "days": days,
                "current_price": cur_price,
                "poc": round(poc_price, 2),
                "poc_volume_pct": round((poc_vol / total_vol) * 100, 1),
                "va": {"low": round(val_price, 2), "high": round(vah_price, 2)},
                "vah": round(vah_price, 2),
                "val": round(val_price, 2),
                "hvn": hvns[:10],
                "lvn": lvns[:10],
                "volume_gaps": volume_gaps,
                "current_in_va": val_price <= cur_price <= vah_price,
            }
        except Exception as exc:
            logger.debug(f"[UnifiedDataPool] volume_profile_v2 {symbol}: {exc}")
            return {"buckets": [], "error": str(exc)}

    @staticmethod
    def compute_volume_profile_v3(
        symbol: str,
        *,
        days: int = 30,
        bucket_count: int = 50,
        va_pct: float = 0.70,
        use_rolling: bool = True,
        rolling_window: int = 7,
    ) -> Dict:
        """
        VPVR v3 — 滚动窗口 + POC迁移 + VA收窄检测 + 多周期VP

        v3 相比 v2 新增：
        1. **滚动窗口**: 每 rolling_window 天独立计算VP，检测POC迁移
        2. **POC迁移追踪**: 记录POC位置如何随时间漂移
        3. **VA收窄检测**: 监控Value Area宽度变化（收窄=突破前兆）
        4. **多周期VP**: 同时计算1h/4h两个周期的VP进行对比
        5. **加密适配**: 更短窗口(7d)、更敏感阈值

        Returns:
            {
                ...v2 fields,
                "poc_migration": [...],      # POC历史迁移
                "va_narrowing": bool,         # VA是否在收窄
                "va_width_history": [...],    # VA宽度历史
                "multi_period": {             # 多周期VP
                    "1h": {...},
                    "4h": {...},
                },
                "breakout_risk": "low|medium|high",  # 突破风险评估
                "regime": "accumulation|distribution|trending",
            }
        """
        import numpy as np

        try:
            # 基础v2计算
            base = UnifiedDataPool.compute_volume_profile_v2(
                symbol, days=days, bucket_count=bucket_count, va_pct=va_pct
            )
            if base.get("insufficient_data") or not base.get("buckets"):
                return {**base, "v3_enhanced": False}

            klines = unified_data_pool.get_kline_series(
                symbol, interval="1h", limit=24 * days
            )
            if not klines or len(klines) < 100:
                return {**base, "v3_enhanced": False}

            closes = [float(k.close) for k in klines]
            highs = [float(k.high) for k in klines]
            lows = [float(k.low) for k in klines]
            volumes = [float(k.volume or 0) for k in klines]
            total_bars = len(closes)

            # ── 1. 滚动窗口POC迁移 ──
            poc_migration = []
            va_width_history = []
            bars_per_day = 24
            window_bars = rolling_window * bars_per_day
            step_bars = max(1, bars_per_day)  # 每天滚动一步

            for start_idx in range(0, total_bars - window_bars, step_bars):
                end_idx = start_idx + window_bars
                window_closes = closes[start_idx:end_idx]
                window_highs = highs[start_idx:end_idx]
                window_lows = lows[start_idx:end_idx]
                window_vols = volumes[start_idx:end_idx]

                if len(window_closes) < 20:
                    break

                total_vol = sum(window_vols)
                if total_vol <= 0:
                    continue

                min_p = min(window_lows)
                max_p = max(window_highs)
                p_range = max_p - min_p
                if p_range <= 0:
                    continue

                b_size = p_range / bucket_count
                window_buckets: Dict[int, float] = {}

                for h, l, v in zip(window_highs, window_lows, window_vols):
                    if v <= 0:
                        continue
                    lo_i = max(0, min(int((l - min_p) / b_size), bucket_count - 1))
                    hi_i = max(0, min(int((h - min_p) / b_size), bucket_count - 1))
                    if hi_i == lo_i:
                        window_buckets[lo_i] = window_buckets.get(lo_i, 0.0) + v
                    else:
                        share = v / (hi_i - lo_i + 1)
                        for bi in range(lo_i, hi_i + 1):
                            window_buckets[bi] = window_buckets.get(bi, 0.0) + share

                if not window_buckets:
                    continue

                poc_i = max(window_buckets, key=window_buckets.get)
                poc_p = min_p + (poc_i + 0.5) * b_size

                # VA宽度
                target_v = total_vol * va_pct
                acc = window_buckets[poc_i]
                li = ri = poc_i
                while acc < target_v:
                    lv = window_buckets.get(li - 1, 0)
                    rv = window_buckets.get(ri + 1, 0)
                    if lv >= rv and li > min(window_buckets.keys()):
                        li -= 1
                        acc += lv
                    elif ri < max(window_buckets.keys()):
                        ri += 1
                        acc += rv
                    elif li > min(window_buckets.keys()):
                        li -= 1
                        acc += lv
                    else:
                        break
                va_width_pct = ((ri - li + 1) * b_size / p_range) * 100

                poc_migration.append({
                    "window_start_day": round(start_idx / bars_per_day, 1),
                    "poc": round(poc_p, 2),
                    "va_width_pct": round(va_width_pct, 1),
                })
                va_width_history.append(va_width_pct)

            # ── 2. VA收窄检测 ──
            va_narrowing = False
            narrowing_severity = "low"
            if len(va_width_history) >= 3:
                recent_3 = va_width_history[-3:]
                prev_3 = va_width_history[:-3][-3:] if len(va_width_history) >= 6 else va_width_history[:3]
                if recent_3 and prev_3:
                    recent_avg = float(np.mean(recent_3))
                    prev_avg = float(np.mean(prev_3))
                    if prev_avg > 0 and recent_avg < prev_avg * 0.7:
                        va_narrowing = True
                        narrowing_severity = "high" if recent_avg < prev_avg * 0.5 else "medium"

            # ── 3. POC迁移方向 ──
            poc_direction = "stable"
            if len(poc_migration) >= 4:
                first_poc = poc_migration[0]["poc"]
                last_poc = poc_migration[-1]["poc"]
                if last_poc > first_poc * 1.02:
                    poc_direction = "rising"
                elif last_poc < first_poc * 0.98:
                    poc_direction = "falling"

            # ── 4. 多周期VP ──
            multi_period = {}
            for tf_interval, tf_days, tf_buckets in [
                ("1h", min(7, days), min(bucket_count, 30)),
                ("4h", min(14, days), min(bucket_count, 25)),
            ]:
                try:
                    tf_klines = unified_data_pool.get_kline_series(
                        symbol, interval=tf_interval, limit=6 * tf_days
                    )
                    if tf_klines and len(tf_klines) >= 20:
                        tf_close = [float(k.close) for k in tf_klines]
                        tf_high = [float(k.high) for k in tf_klines]
                        tf_low = [float(k.low) for k in tf_klines]
                        tf_vol = [float(k.volume or 0) for k in tf_klines]

                        # 简版POC计算
                        tf_total = sum(tf_vol)
                        if tf_total > 0:
                            tf_min = min(tf_low)
                            tf_max = max(tf_high)
                            tf_range = tf_max - tf_min
                            if tf_range > 0:
                                tf_bs = tf_range / tf_buckets
                                tf_b: Dict[int, float] = {}
                                for h, l, v in zip(tf_high, tf_low, tf_vol):
                                    if v <= 0:
                                        continue
                                    bi = max(0, min(int((h + l) / 2 - tf_min) / tf_bs, tf_buckets - 1))
                                    tf_b[bi] = tf_b.get(bi, 0.0) + v
                                if tf_b:
                                    tf_poc_i = max(tf_b, key=tf_b.get)
                                    tf_poc_price = tf_min + (tf_poc_i + 0.5) * tf_bs
                                    cur = tf_close[-1]
                                    multi_period[tf_interval] = {
                                        "poc": round(tf_poc_price, 2),
                                        "current_vs_poc": "above" if cur > tf_poc_price else "below",
                                        "gap_pct": round(abs(cur - tf_poc_price) / tf_poc_price * 100, 2),
                                    }
                except Exception:
                    pass

            # ── 5. 突破风险评估 ──
            curent_price = base.get("current_price", 0)
            va = base.get("va", {})
            val = va.get("low", 0)
            vah = va.get("high", 0)

            breakout_risk = "medium"
            if va_narrowing and narrowing_severity == "high":
                breakout_risk = "high"
            elif va_narrowing:
                breakout_risk = "medium"
            elif curent_price and val and vah:
                if curent_price > vah * 1.02 or curent_price < val * 0.98:
                    breakout_risk = "high"
                elif val <= curent_price <= vah:
                    breakout_risk = "low"

            # ── 6. 区制判断 ──
            if poc_direction == "stable" and va_narrowing:
                regime = "accumulation"
            elif poc_direction == "rising":
                regime = "trending"
            elif poc_direction == "falling":
                regime = "distribution"
            else:
                regime = "trending"

            return {
                **base,
                "v3_enhanced": True,
                "poc_migration": poc_migration,
                "va_narrowing": va_narrowing,
                "va_width_history": va_width_history[-10:],
                "multi_period": multi_period,
                "breakout_risk": breakout_risk,
                "regime": regime,
                "poc_direction": poc_direction,
                "rolling_window_days": rolling_window,
            }

        except Exception as exc:
            logger.debug(f"[UnifiedDataPool] volume_profile_v3 {symbol}: {exc}")
            return {"buckets": [], "error": str(exc), "v3_enhanced": False}


# 全局单例
unified_data_pool = UnifiedDataPool()


def get_unified_data_pool() -> UnifiedDataPool:
    """获取统一数据池实例"""
    return unified_data_pool


# ─────────────────────────────────────────────────────────────────
#  Phase 2.5：DataHub 标准接口层（方案§3B.3）
#  业务模块只通过以下标准接口取数，禁止绕过。
# ─────────────────────────────────────────────────────────────────

class DataHub:
    """
    统一数据中枢 — 单一数据入口（Phase 2.5）

    所有读市场/账户/持仓/K线/衍生品/情绪的调用均经此中枢，
    禁止业务层直接调用 hyperliquid_market_data / trading_client.get_positions。

    标准接口：
        get_market(symbols, environment)     → Dict[str, MarketTick]
        get_account(account_id, environment) → AccountState
        get_positions(account_id, environment) → List[Position]
        get_klines(symbol, interval, count, environment) → list
        get_derivatives(symbols)             → Dict[str, dict]
        get_sentiment(symbols)               → Dict[str, dict]
        get_snapshot(symbols, account_id, options) → UnifiedSnapshot

    内部缓存 TTL：价格1.5s / 账户持仓10s / 衍生品15s / 情绪60s
    """

    _instance = None
    _lock = __import__("threading").Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        import threading
        self._price_cache: dict = {}          # {symbol: {"price": float, "ts": float, "data": dict}}
        self._account_cache: dict = {}        # {(account_id, env): {"ts": float, "data": dict}}
        self._positions_cache: dict = {}      # {(account_id, env): {"ts": float, "data": list}}
        self._kline_cache: dict = {}          # {(symbol, interval): {"ts": float, "data": list}}
        self._derivatives_cache: dict = {}    # {symbol: {"ts": float, "data": dict}}
        self._sentiment_cache: dict = {}      # {symbol: {"ts": float, "data": dict}}
        self._ttl_price = 1.5
        self._ttl_account = 10.0
        self._ttl_kline = 60.0
        self._ttl_derivatives = 600.0  # D7: 衍生品 10 分钟不变（原 15s → 减少 API 调用）
        self._ttl_sentiment = 60.0
        self._lock = threading.Lock()
        logger.info("[DataHub] 统一数据中枢初始化完成（Phase 2.5）")

    def get_market(self, symbols: list, environment: str = "mainnet") -> dict:
        """
        获取价格/量/资金费率/OI（TTL=1.5s）。
        Returns: {symbol: {"price": float, "funding_rate": float, "volume_24h": float, ...}}
        """
        import time
        now = time.time()
        result = {}
        need_fetch = []
        with self._lock:
            for sym in symbols:
                cached = self._price_cache.get(sym)
                if cached and (now - cached["ts"]) < self._ttl_price:
                    result[sym] = cached["data"]
                else:
                    need_fetch.append(sym)

        if need_fetch:
            fresh = self._fetch_market(need_fetch, environment)
            with self._lock:
                for sym, data in fresh.items():
                    self._price_cache[sym] = {"ts": now, "data": data}
                    result[sym] = data
        return result

    def get_account(self, account_id: int, environment: str = "mainnet") -> dict:
        """
        获取账户权益/可用余额/保证金使用率（TTL=10s）。
        Returns: {"total_equity": float, "available_balance": float, ...}
        """
        import time
        now = time.time()
        key = (account_id, environment)
        with self._lock:
            cached = self._account_cache.get(key)
            if cached and (now - cached["ts"]) < self._ttl_account:
                return cached["data"]
        fresh = self._fetch_account(account_id, environment)
        with self._lock:
            self._account_cache[key] = {"ts": now, "data": fresh}
        return fresh

    def get_positions(self, account_id: int, environment: str = "mainnet") -> list:
        """
        获取持仓列表（TTL=10s）。
        Returns: [{"coin": str, "szi": float, "entry_px": float, ...}]
        """
        import time
        now = time.time()
        key = (account_id, environment)
        with self._lock:
            cached = self._positions_cache.get(key)
            if cached and (now - cached["ts"]) < self._ttl_account:
                return cached["data"]
        fresh = self._fetch_positions(account_id, environment)
        with self._lock:
            self._positions_cache[key] = {"ts": now, "data": fresh}
        return fresh

    def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        count: int = 200,
        environment: str = "mainnet",
    ) -> list:
        """
        获取K线（TTL=60s）。
        Returns: [{"timestamp": int, "open": float, "high": float, "low": float, "close": float, "volume": float}]
        """
        import time
        now = time.time()
        key = (symbol, interval)
        with self._lock:
            cached = self._kline_cache.get(key)
            if cached and (now - cached["ts"]) < self._ttl_kline:
                return cached["data"]
        fresh = self._fetch_klines(symbol, interval, count, environment)
        with self._lock:
            self._kline_cache[key] = {"ts": now, "data": fresh}
        return fresh

    def get_derivatives(self, symbols: list) -> dict:
        """
        获取OI/资金费率/清算等衍生品数据（TTL=15s）。
        Returns: {symbol: {"funding_rate": float, "oi_change_1h_pct": float, ...}}
        """
        import time
        now = time.time()
        result = {}
        need_fetch = []
        with self._lock:
            for sym in symbols:
                cached = self._derivatives_cache.get(sym)
                if cached and (now - cached["ts"]) < self._ttl_derivatives:
                    result[sym] = cached["data"]
                else:
                    need_fetch.append(sym)
        if need_fetch:
            fresh = self._fetch_derivatives(need_fetch)
            with self._lock:
                for sym, data in fresh.items():
                    self._derivatives_cache[sym] = {"ts": now, "data": data}
                    result[sym] = data
        return result

    def get_sentiment(self, symbols: list) -> dict:
        """
        获取综合情绪数据（TTL=60s）。
        Returns: {symbol: {"composite_index": float, "zone": str, ...}}
        """
        import time
        now = time.time()
        result = {}
        need_fetch = []
        with self._lock:
            for sym in symbols:
                cached = self._sentiment_cache.get(sym)
                if cached and (now - cached["ts"]) < self._ttl_sentiment:
                    result[sym] = cached["data"]
                else:
                    need_fetch.append(sym)
        if need_fetch:
            fresh = self._fetch_sentiment(need_fetch)
            with self._lock:
                for sym, data in fresh.items():
                    self._sentiment_cache[sym] = {"ts": now, "data": data}
                    result[sym] = data
        return result

    def get_snapshot(
        self,
        symbols: list,
        account_id: int = None,
        environment: str = "mainnet",
        include_klines: bool = True,
    ) -> "UnifiedSnapshot":
        """
        获取一次性一致决策快照（不缓存，按次拉取）。
        委托给现有 UnifiedDataPool.capture_snapshot。
        """
        return unified_data_pool.capture_snapshot(
            symbols=symbols,
            account_id=account_id,
            environment=environment,
            include_klines=include_klines,
            include_strategy=True,
        )

    def invalidate_account_cache(self, account_id: int, environment: str = "mainnet"):
        """执行层下单后主动使账户/持仓缓存失效（立即重拉）"""
        key = (account_id, environment)
        with self._lock:
            self._account_cache.pop(key, None)
            self._positions_cache.pop(key, None)
        logger.debug(f"[DataHub] 账户缓存已失效: account_id={account_id}")

    # ── 底层数据拉取（仅中枢内部使用）──

    def _fetch_market(self, symbols: list, environment: str) -> dict:
        try:
            from backend.services.market_data import get_last_price, get_ticker_data
            result = {}
            for sym in symbols:
                try:
                    ticker = get_ticker_data(sym, environment)
                    result[sym] = {
                        "price": float(ticker.get("last", 0) or 0),
                        "funding_rate": float(ticker.get("funding_rate", 0) or 0),
                        "volume_24h": float(ticker.get("volume_24h", 0) or 0),
                        "open_interest": float(ticker.get("open_interest", 0) or 0),
                        # [P2-修复] ticker 源（market_data / hyperliquid_market_data）返回
                        # 的字段名是 percentage24h（小数百分比），此前读 price_change_24h_pct
                        # 恒 0，导致 24h 涨跌幅不可信。兼容两种命名。
                        "price_change_24h_pct": float(
                            ticker.get("price_change_24h_pct")
                            or ticker.get("percentage24h")
                            or 0
                        ),
                    }
                except Exception:
                    price = get_last_price(sym)
                    result[sym] = {"price": float(price or 0)}
            return result
        except Exception as e:
            logger.debug(f"[DataHub] _fetch_market error: {e}")
            return {}

    def _fetch_account(self, account_id: int, environment: str) -> dict:
        try:
            from backend.database.connection import SessionLocal
            from backend.services.hyperliquid_trading_client import get_hyperliquid_client
            db = SessionLocal()
            try:
                client = get_hyperliquid_client(db, account_id)
                if not client:
                    return {}
                balance = client.get_account_balance(db)
                return {
                    "account_id": account_id,
                    "total_equity": float(balance.get("total_balance", 0) or 0),
                    "available_balance": float(balance.get("available_balance", 0) or 0),
                    "used_margin": float(balance.get("margin_used", 0) or 0),
                    "margin_usage_pct": float(balance.get("margin_usage_ratio", 0) or 0),
                }
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[DataHub] _fetch_account error: {e}")
            return {}

    def _fetch_positions(self, account_id: int, environment: str) -> list:
        try:
            from backend.database.connection import SessionLocal
            from backend.services.hyperliquid_trading_client import get_hyperliquid_client
            db = SessionLocal()
            try:
                client = get_hyperliquid_client(db, account_id)
                if not client:
                    return []
                return client.get_positions(db) or []
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[DataHub] _fetch_positions error: {e}")
            return []

    def _fetch_klines(self, symbol: str, interval: str, count: int, environment: str) -> list:
        """数据中台整改：走 data_center 统一入口（多交易所择优）。"""
        try:
            from backend.services.data_center import data_center
            result = data_center.get_klines(symbol, interval, count=count)
            return result.rows or []
        except Exception:
            pass
        try:
            from backend.services.market_data import get_kline_data
            return get_kline_data(symbol, interval, count) or []
        except Exception as e:
            logger.debug(f"[DataHub] _fetch_klines error: {e}")
            return []

    def get_klines_history(
        self,
        symbol: str,
        interval: str = "1d",
        start_ts: int = 0,
        end_ts: int = 0,
        exchanges: list = None,
    ) -> list:
        """
        全历史 K线查询（多交易所对比择优）—— 数据中台统一入口。

        所有需要历史数据的（因子回测/CPCV/IC评估）都应走此方法，不绕开直连 DB。

        逻辑：
            1. 遍历所有交易所查同一品种同一周期
            2. 按时间范围过滤
            3. 取数据最深的那个交易所（根数最多 = 历史最全）
            4. 缓存结果

        Args:
            symbol: 如 "BTC"
            interval: "1d"/"4h"/"1h"/"5m"
            start_ts/end_ts: Unix 秒，0=不限
            exchanges: 查询的交易所列表，None=全部已知所
        Returns:
            [{"timestamp":int, "open":float, ...}]，按时间正序
        """
        import time
        if exchanges is None:
            exchanges = ["hyperliquid", "asterdex", "binance", "bybit", "okx"]

        cache_key = ("history", symbol.upper(), interval, start_ts, end_ts)
        now = time.time()
        with self._lock:
            cached = self._kline_cache.get(cache_key)
            if cached and (now - cached["ts"]) < self._ttl_kline:
                return cached["data"]

        try:
            from sqlalchemy import text as sa_text

            from backend.database.connection import MarketSessionLocal
        except Exception:
            return []

        best_klines: list = []
        best_exchange: str = ""
        best_count: int = 0

        try:
            db = MarketSessionLocal()
            try:
                for ex in exchanges:
                    try:
                        conditions = "exchange = :ex AND symbol = :sym AND period = :p"
                        params = {"ex": ex, "sym": symbol.upper(), "p": interval}
                        if start_ts:
                            conditions += " AND timestamp >= :start"
                            params["start"] = start_ts
                        if end_ts:
                            conditions += " AND timestamp <= :end"
                            params["end"] = end_ts

                        rows = db.execute(sa_text(
                            f"SELECT timestamp, open_price, high_price, low_price, close_price, volume "
                            f"FROM crypto_klines WHERE {conditions} ORDER BY timestamp"
                        ), params).fetchall()

                        if len(rows) > best_count:
                            best_count = len(rows)
                            best_exchange = ex
                            best_klines = [{
                                "timestamp": r[0],
                                "open": float(r[1] or 0), "high": float(r[2] or 0),
                                "low": float(r[3] or 0), "close": float(r[4] or 0),
                                "volume": float(r[5] or 0),
                            } for r in rows if r[0] is not None and r[1] is not None]
                    except Exception:
                        continue
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[DataHub] get_klines_history error: {e}")

        if best_count > 0:
            logger.info(
                f"[DataHub] {symbol}/{interval}: 多交易所择优 → {best_exchange} "
                f"({best_count} roots)"
            )

        with self._lock:
            self._kline_cache[cache_key] = {"ts": now, "data": best_klines}
        return best_klines

    def get_data_coverage(self, symbol: str, interval: str = "1d") -> dict:
        """
        数据覆盖报告（多交易所）—— 数据中台统一入口。

        Returns: {exchange: {count, first, last, years}}
        """
        try:
            from sqlalchemy import text as sa_text

            from backend.database.connection import MarketSessionLocal
            db = MarketSessionLocal()
            try:
                rows = db.execute(sa_text(
                    "SELECT exchange, COUNT(*), MIN(timestamp), MAX(timestamp) "
                    "FROM crypto_klines WHERE symbol=:sym AND period=:p "
                    "GROUP BY exchange ORDER BY COUNT(*) DESC"
                ), {"sym": symbol.upper(), "p": interval}).fetchall()
                result = {}
                for r in rows:
                    years = (r[3] - r[2]) / (365.25 * 86400) if r[2] and r[3] else 0
                    result[r[0]] = {"count": r[1], "first_ts": r[2], "last_ts": r[3], "years": round(years, 1)}
                return result
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[DataHub] get_data_coverage error: {e}")
            return {}

    def _fetch_derivatives(self, symbols: list) -> dict:
        try:
            from backend.database.connection import SessionLocal
            from backend.services.derivatives_analytics_service import derivatives_analytics
            db = SessionLocal()
            try:
                result = {}
                for sym in symbols:
                    try:
                        snap = derivatives_analytics.get_snapshot(db, sym)
                        if snap:
                            result[sym] = snap if isinstance(snap, dict) else vars(snap)
                    except Exception:
                        result[sym] = {}
                return result
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[DataHub] _fetch_derivatives error: {e}")
            return {}

    def _fetch_sentiment(self, symbols: list) -> dict:
        try:
            from backend.services.sentiment_composite_service import sentiment_composite_service
            result = {}
            for sym in symbols:
                try:
                    sent = sentiment_composite_service.get_sentiment(sym)
                    result[sym] = sent if isinstance(sent, dict) else {}
                except Exception:
                    result[sym] = {}
            return result
        except Exception as e:
            logger.debug(f"[DataHub] _fetch_sentiment error: {e}")
            return {}


# DataHub 全局单例
data_hub = DataHub()


def get_data_hub() -> DataHub:
    """获取统一数据中枢实例"""
    return data_hub

