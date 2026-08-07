"""
合约数据分析服务 — OI / 资金费率 / 清算 / 多空比

免费数据源优先级:
1. Hyperliquid 原生 API（资金费率、OI、标记价格 — 免费无需Key）
2. Binance 公开 API（资金费率、OI历史、多空比 — 免费无需Key）
3. Coinalyze API（清算、多空比、OI — 免费注册即可）
4. 本地 MarketFlowIndicators / MarketAssetMetrics（WebSocket已采集的数据）
5. Coinglass API（v6 2.3 数据源抽象层 — 免费→付费无缝切换）
"""
import logging
import os
import time
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

import httpx

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_FUTURES_DATA = "https://fapi.binance.com/futures/data"
COINALYZE_BASE = "https://api.coinalyze.net/v1"
HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"

# Coinalyze 交易所代码: Binance=A, Bybit=6, OKX=O, Hyperliquid=HL
COINALYZE_SYMBOL_MAP = {
    "BTC": "BTCUSDT_PERP.A",
    "ETH": "ETHUSDT_PERP.A",
    "SOL": "SOLUSDT_PERP.A",
    "XRP": "XRPUSDT_PERP.A",
    "DOGE": "DOGEUSDT_PERP.A",
    "ARB": "ARBUSDT_PERP.A",
    "OP": "OPUSDT_PERP.A",
    "AVAX": "AVAXUSDT_PERP.A",
    "LINK": "LINKUSDT_PERP.A",
    "ADA": "ADAUSDT_PERP.A",
    "MATIC": "MATICUSDT_PERP.A",
    "DOT": "DOTUSDT_PERP.A",
    "NEAR": "NEARUSDT_PERP.A",
    "FIL": "FILUSDT_PERP.A",
}


@dataclass
class DerivativesSnapshot:
    symbol: str
    timestamp: float = 0.0
    # 资金费率
    funding_rate: float = 0.0
    funding_rate_8h_avg: float = 0.0
    funding_rate_percentile: float = 50.0
    predicted_funding_rate: float = 0.0
    # 持仓量
    oi_total: float = 0.0
    oi_change_1h: float = 0.0
    oi_change_24h: float = 0.0
    # 清算
    liquidation_1h_long: float = 0.0
    liquidation_1h_short: float = 0.0
    liquidation_ratio: float = 1.0
    # 多空比
    long_short_ratio: float = 1.0
    top_trader_ls_ratio: float = 1.0
    # 综合信号
    signal: str = "neutral"
    signal_strength: float = 0.0
    interpretation: str = ""
    data_sources: str = ""


ANALYSIS_MATRIX = [
    ("up",   "up",   False, "bullish",  "多头主导，趋势健康"),
    ("up",   "down", False, "bearish",  "空头建仓，继续看跌"),
    ("down", "down", True,  "bullish",  "多头爆仓，可能见底"),
    ("down", "up",   True,  "bearish",  "空头爆仓，可能见顶"),
    ("flat", "flat", False, "neutral",  "市场犹豫，等待方向"),
]


class DerivativesAnalyticsService:
    """合约数据分析服务（单例）— 使用免费数据源"""

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
        self._coinalyze_key: str = self._load_coinalyze_key()
        self._proxy: Optional[str] = os.environ.get("BINANCE_HTTPS_PROXY") or None
        self._cache: Dict[str, DerivativesSnapshot] = {}
        self._cache_ts: Dict[str, float] = {}
        self._cache_ttl = 60
        # 后台刷新去重标记：get_cached_snapshot 触发的异步刷新，同 key 刷新中不重复起线程
        self._bg_refreshing: Dict[str, bool] = {}
        # 错误降频计数器
        self._error_counters: Dict[str, int] = {}
        self._error_last_logged: Dict[str, float] = {}
        _proxy_status = f"proxy={self._proxy}" if self._proxy else "直连(无代理)"
        logger.info(
            f"[DerivAnalytics] 合约数据分析服务初始化完成 "
            f"(Coinalyze={'有Key' if self._coinalyze_key else '无Key'}, "
            f"{_proxy_status})"
        )

    @staticmethod
    def _load_coinalyze_key() -> str:
        """从环境变量或数据库加载 Coinalyze API Key"""
        key = os.environ.get("COINALYZE_API_KEY", "")
        if key:
            return key
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SystemConfig
            db = SessionLocal()
            try:
                cfg = db.query(SystemConfig).filter(SystemConfig.key == "COINALYZE_API_KEY").first()
                if cfg and cfg.value:
                    os.environ["COINALYZE_API_KEY"] = cfg.value
                    return cfg.value
            finally:
                db.close()
        except Exception:
            pass
        return ""

    def _get_client(self, timeout: int = 10) -> httpx.Client:
        return httpx.Client(timeout=timeout, proxy=self._proxy)

    def _throttled_warning(self, key: str, msg: str, interval: float = 60.0):
        """降频告警：同一 key 每 interval 秒最多输出一次 warning，其余累计抑制"""
        now = time.time()
        last = self._error_last_logged.get(key, 0)
        if now - last >= interval:
            suppressed = self._error_counters.get(key, 0)
            if suppressed > 0:
                logger.warning(f"{msg} [已抑制 {suppressed} 条]")
            else:
                logger.warning(msg)
            self._error_counters[key] = 0
            self._error_last_logged[key] = now
        else:
            self._error_counters[key] = self._error_counters.get(key, 0) + 1

    # ════════════════════════════════════════════
    #  公开接口
    # ════════════════════════════════════════════

    def get_snapshot(self, symbol: str = "BTC") -> DerivativesSnapshot:
        """获取指定币种的合约快照（带缓存）"""
        now = time.time()
        key = symbol.upper()
        if key in self._cache and now - self._cache_ts.get(key, 0) < self._cache_ttl:
            return self._cache[key]

        # 防止缓存无限增长
        if len(self._cache) > 100:
            oldest = sorted(self._cache_ts.items(), key=lambda x: x[1])[:50]
            for ok, _ in oldest:
                self._cache.pop(ok, None)
                self._cache_ts.pop(ok, None)

        snap = self._build_snapshot(key)
        self._cache[key] = snap
        self._cache_ts[key] = now
        return snap

    def get_cached_snapshot(
        self, symbol: str = "BTC", max_stale: float = 600.0
    ) -> Optional[DerivativesSnapshot]:
        """只读缓存快照，【绝不】在调用线程里同步拉网络（stale-while-revalidate）。

        用于 scalp 等高频热路径：get_snapshot 缓存 miss 时会同步串行拉
        Hyperliquid/Binance/Coinalyze（每个 timeout=10s），实测单次可达 12s，
        直接把短线单币扫描卡到 20s+、并因超时使长期持有的 DB 连接被服务端 90s
        idle_in_transaction 掐断。本方法只读缓存：
          - 命中且未超 max_stale（默认 10min，funding/OI 变化慢，足够新）→ 返回缓存；
          - 否则返回现有(可能过期)值或 None，并【后台】异步刷新，让下次调用命中新值。
        调用方对 None 只需跳过对应因子（has_data=False 自动过滤），不报错、不阻塞。
        """
        key = symbol.upper()
        now = time.time()
        snap = self._cache.get(key)
        ts = self._cache_ts.get(key, 0)
        if snap is not None and now - ts < max_stale:
            return snap
        # 后台刷新（去重：同 key 正在刷新则不再起线程），当前调用不阻塞
        if not self._bg_refreshing.get(key):
            self._bg_refreshing[key] = True

            def _bg_refresh():
                try:
                    self.get_snapshot(key)  # 构建+落缓存（网络在后台线程里发生）
                except Exception:
                    pass
                finally:
                    self._bg_refreshing[key] = False

            threading.Thread(
                target=_bg_refresh, daemon=True, name=f"deriv-refresh-{key}"
            ).start()
        return snap  # 过期值或 None：热路径不阻塞，宁可暂缺一个 funding 因子

    def get_all_snapshots(self, symbols: List[str] = None) -> Dict[str, DerivativesSnapshot]:
        symbols = symbols or ["BTC", "ETH"]
        return {s: self.get_snapshot(s) for s in symbols}

    # ════════════════════════════════════════════
    #  数据构建
    # ════════════════════════════════════════════

    def _build_snapshot(self, symbol: str) -> DerivativesSnapshot:
        snap = DerivativesSnapshot(symbol=symbol, timestamp=time.time())
        sources = []

        # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连
        # HL/Binance/Coinalyze 实时 API，只保留本地落库数据（market_flow_indicators
        # 与 market_asset_metrics，由数据中心采集器持续写入），保证唯一数据源。
        from backend.services.market_data import _dc_only_enabled
        dc_only = _dc_only_enabled()

        # Layer 1: 本地 MarketFlowIndicators（已有WebSocket数据）
        if self._fill_from_local(snap):
            sources.append("local")

        # Layer 2: Hyperliquid 原生 API（免费、无Key）
        if not dc_only and self._fill_from_hyperliquid(snap):
            sources.append("hyperliquid")

        # Layer 3: Binance 公开 API（免费、无Key）
        if not dc_only and self._fill_from_binance(snap):
            sources.append("binance")

        # Layer 4: Coinalyze API（免费注册、清算数据）
        if not dc_only and self._coinalyze_key and self._fill_from_coinalyze(snap):
            sources.append("coinalyze")

        # Layer 5: Coinglass（v6 2.3 统一 DataProvider：免费→付费无缝切换）
        if not dc_only and self._fill_from_coinglass(snap):
            sources.append("coinglass")

        if not sources:
            self._throttled_warning(f"all_failed:{symbol}", f"[DerivAnalytics] {symbol}: 所有数据源均失败! proxy={self._proxy}, coinalyze_key={'有' if self._coinalyze_key else '无'}")

        # 后备：当 oi_change_1h 仍为0但 oi_total 有值时，直接从数据库历史记录计算
        if snap.oi_change_1h == 0 and snap.oi_total > 0:
            snap.oi_change_1h = self._compute_oi_change_from_raw(symbol, snap.oi_total)

        snap.data_sources = ",".join(sources)
        self._analyze(snap)
        logger.info(f"[DerivAnalytics] {symbol}: sources={snap.data_sources}, liq={snap.liquidation_1h_long:.0f}/{snap.liquidation_1h_short:.0f}, funding={snap.funding_rate}")
        return snap

    # ────────────── Layer 1: 本地数据 ──────────────

    def _fill_from_local(self, snap: DerivativesSnapshot) -> bool:
        filled = False
        try:
            from backend.services.market_flow_indicators import get_indicator_value
            from backend.database.connection import SessionLocal
            db = SessionLocal()
            try:
                funding = get_indicator_value(db, snap.symbol, "FUNDING", "15m")
                if funding is not None:
                    snap.funding_rate = funding
                    filled = True
                oi = get_indicator_value(db, snap.symbol, "OI", "1h")
                if oi is not None and oi > 0:
                    snap.oi_total = oi
                    filled = True
                oi_prev = get_indicator_value(db, snap.symbol, "OI", "4h")
                if oi_prev and oi_prev > 0 and snap.oi_total > 0:
                    snap.oi_change_1h = (snap.oi_total - oi_prev) / oi_prev
                    filled = True
            finally:
                db.close()
        except Exception as e:
            self._throttled_warning("local_error", f"[DerivAnalytics] 本地数据不可用: {e}")
        return filled

    def _compute_oi_change_from_raw(self, symbol: str, current_oi: float) -> float:
        """当指标层缓存失效时，直接从 market_asset_metrics 计算 1h OI 变化"""
        try:
            from backend.database.models import MarketAssetMetrics
            from backend.database.connection import MarketSessionLocal
            import time as _time
            db = MarketSessionLocal()
            try:
                one_hour_ago_ms = int(_time.time() * 1000) - 3600_000
                prev_row = db.query(MarketAssetMetrics.open_interest).filter(
                    MarketAssetMetrics.symbol == symbol.upper(),
                    MarketAssetMetrics.timestamp <= one_hour_ago_ms,
                    MarketAssetMetrics.open_interest > 0,
                ).order_by(MarketAssetMetrics.timestamp.desc()).first()
                if prev_row and prev_row[0]:
                    prev_oi = float(prev_row[0])
                    if prev_oi > 0:
                        change = (current_oi - prev_oi) / prev_oi
                        logger.info(
                            f"[DerivAnalytics] {symbol} raw OI change fallback: "
                            f"{prev_oi:.2f} → {current_oi:.2f} = {change:+.4f}")
                        return change
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[DerivAnalytics] raw OI change fallback failed: {e}")
        return 0.0

    # ────────────── Layer 2: Hyperliquid 原生 ──────────────

    def _fill_from_hyperliquid(self, snap: DerivativesSnapshot) -> bool:
        """从 Hyperliquid REST API 获取资金费率和 OI（免费无Key）"""
        filled = False
        try:
            with self._get_client() as client:
                # metaAndAssetCtxs: 包含 funding, OI, mark price
                r = client.post(HYPERLIQUID_INFO, json={"type": "metaAndAssetCtxs"})
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list) and len(data) >= 2:
                        universe = data[0].get("universe", [])
                        ctxs = data[1]
                        for i, meta in enumerate(universe):
                            if meta.get("name", "").upper() == snap.symbol and i < len(ctxs):
                                ctx = ctxs[i]
                                # [2026-07-10 资金费率修复] 原条件 snap.funding_rate == 0
                                # 导致 Layer1 本地脏数据(被×100放大)挡住 Hyperliquid 实时值。
                                # 改为始终用 Hyperliquid 实时值覆盖本地缓存，保证数据新鲜。
                                if ctx.get("funding"):
                                    snap.funding_rate = float(ctx["funding"])
                                if snap.oi_total == 0 and ctx.get("openInterest"):
                                    snap.oi_total = float(ctx["openInterest"])
                                if ctx.get("markPx"):
                                    snap.funding_rate_percentile = 50.0
                                filled = True
                                break

                # predictedFundings: [[coin, [[venue, {fundingRate, nextFundingTime, fundingIntervalHours}], ...]], ...]
                # [2026-07-10 资金费率修复] 原代码读 vegaRate/sampleRate 字段（已不存在），
                # 实际字段是 fundingRate → predicted_funding_rate 恒为 0。
                # 且各 venue 的费率不同：HlPerp 是 Hyperliquid 固定值(无市场区分度)，
                # BinPerp/BybitPerp 才反映真实多空拥挤度。优先取 Binance，其次 Bybit，
                # 最后才 HlPerp；统一换算成"每小时费率"便于横向比较。
                try:
                    r2 = client.post(HYPERLIQUID_INFO, json={"type": "predictedFundings"})
                    if r2.status_code == 200:
                        predictions = r2.json()
                        if isinstance(predictions, list):
                            for item in predictions:
                                if isinstance(item, list) and len(item) >= 2:
                                    if item[0].upper() == snap.symbol:
                                        venues = item[1]
                                        if isinstance(venues, list) and venues:
                                            # 收集各 venue 的费率，按优先级选取
                                            best_rate = None
                                            for venue in venues:
                                                if isinstance(venue, list) and len(venue) >= 2:
                                                    venue_name = venue[0]
                                                    val = venue[1]
                                                    # 提取 fundingRate（兼容旧字段名）
                                                    if isinstance(val, dict):
                                                        rate_raw = val.get("fundingRate", val.get("vegaRate", val.get("sampleRate")))
                                                        interval_h = val.get("fundingIntervalHours", 8) or 8
                                                    else:
                                                        rate_raw = val
                                                        interval_h = 8
                                                    try:
                                                        rate_per_hour = float(rate_raw or 0) / interval_h
                                                    except (TypeError, ValueError):
                                                        continue
                                                    # 优先级：Binance > Bybit > Hyperliquid（固定值最后）
                                                    if venue_name == "BinPerp":
                                                        best_rate = ("BinPerp", rate_per_hour)
                                                        break  # Binance 最优，直接定
                                                    elif venue_name == "BybitPerp" and best_rate is None:
                                                        best_rate = ("BybitPerp", rate_per_hour)
                                                    elif best_rate is None:
                                                        best_rate = (venue_name, rate_per_hour)
                                            if best_rate is not None:
                                                snap.predicted_funding_rate = best_rate[1]
                                                # [2026-07-10 资金费率修复] 真实市场预测费率（各币种不同、
                                                # 反映多空拥挤度）覆盖 Hyperliquid 固定值。Hyperliquid 自身
                                                # funding 对主流币恒为 0.0000125，无市场区分度；用 Binance/
                                                # Bybit 的真实费率作为 AI 决策的主 funding_rate。
                                                snap.funding_rate = best_rate[1]
                                        break
                except Exception:
                    pass

        except Exception as e:
            self._throttled_warning(f"hyperliquid:{snap.symbol}", f"[DerivAnalytics] Hyperliquid API 异常: {e}")
        return filled

    # ────────────── Layer 3: Binance 公开 ──────────────

    def _fill_from_binance(self, snap: DerivativesSnapshot) -> bool:
        """从 Binance 公开 API 获取资金费率、OI历史、多空比（免费无Key）"""
        filled = False
        binance_symbol = f"{snap.symbol}USDT"
        try:
            with self._get_client() as client:
                # 资金费率（如果还没有）
                if snap.funding_rate == 0:
                    try:
                        r = client.get(f"{BINANCE_FAPI}/fapi/v1/fundingRate",
                                       params={"symbol": binance_symbol, "limit": 1})
                        if r.status_code == 200:
                            data = r.json()
                            if data:
                                snap.funding_rate = float(data[-1].get("fundingRate", 0))
                                filled = True
                    except Exception:
                        pass

                # OI 历史（5分钟粒度，最近2条用于计算变化）
                if snap.oi_change_1h == 0:
                    try:
                        r = client.get(f"{BINANCE_FUTURES_DATA}/openInterestHist",
                                       params={"symbol": binance_symbol, "period": "5m", "limit": 13})
                        if r.status_code == 200:
                            data = r.json()
                            if data and len(data) >= 2:
                                latest_oi = float(data[-1].get("sumOpenInterestValue", 0))
                                first_oi = float(data[0].get("sumOpenInterestValue", 0))
                                if first_oi > 0:
                                    snap.oi_change_1h = (latest_oi - first_oi) / first_oi
                                if latest_oi > 0 and snap.oi_total == 0:
                                    snap.oi_total = latest_oi
                                filled = True
                    except Exception:
                        pass

                # 全局多空比
                try:
                    r = client.get(f"{BINANCE_FUTURES_DATA}/globalLongShortAccountRatio",
                                   params={"symbol": binance_symbol, "period": "1h", "limit": 1})
                    if r.status_code == 200:
                        data = r.json()
                        if data:
                            snap.long_short_ratio = float(data[-1].get("longShortRatio", 1))
                            filled = True
                except Exception:
                    pass

                # 大户多空比
                try:
                    r = client.get(f"{BINANCE_FUTURES_DATA}/topLongShortAccountRatio",
                                   params={"symbol": binance_symbol, "period": "1h", "limit": 1})
                    if r.status_code == 200:
                        data = r.json()
                        if data:
                            snap.top_trader_ls_ratio = float(data[-1].get("longShortRatio", 1))
                            filled = True
                except Exception:
                    pass

        except Exception as e:
            self._throttled_warning(f"binance:{snap.symbol}", f"[DerivAnalytics] Binance API 异常: {e}")
        return filled

    # ────────────── Layer 4: Coinalyze（免费注册Key） ──────────────

    def _fill_from_coinalyze(self, snap: DerivativesSnapshot) -> bool:
        """从 Coinalyze 获取清算数据和补充多空比（免费API，需注册Key）"""
        filled = False
        ca_symbol = COINALYZE_SYMBOL_MAP.get(snap.symbol)
        if not ca_symbol:
            ca_symbol = f"{snap.symbol}USDT_PERP.A"

        now_ts = int(time.time())
        # [2026-07-10 清算数据修复] 原查过去1小时(now-3600)常返回空：
        # Coinalyze 按整点聚合，当前小时未结束→history 为空或仅1条不完整数据
        # → liquidation_1h 恒为 0 → AI 判断"清算磁力低"。
        # 改为查过去4小时窗口，取最近已完成的完整小时(倒数第2条)作为 liquidation_1h，
        # 这样无论当前小时是否结束都能拿到真实清算数据。
        four_hours_ago = now_ts - 4 * 3600

        try:
            headers = {"api_key": self._coinalyze_key}
            with self._get_client() as client:
                # 清算数据（Coinalyze 独有免费清算 — 替代 CoinGlass）
                try:
                    r = client.get(f"{COINALYZE_BASE}/liquidation-history",
                                   params={"symbols": ca_symbol, "interval": "1hour",
                                           "from": four_hours_ago, "to": now_ts,
                                           "convert_to_usd": "true"},
                                   headers=headers)
                    if r.status_code == 200:
                        data = r.json()
                        if data and isinstance(data, list) and data[0].get("history"):
                            history = data[0]["history"]
                            if history:
                                # 取最近已完成的完整小时（倒数第2条）；
                                # history[-1] 通常是当前未结束的小时，数据不完整
                                completed = history[-2] if len(history) >= 2 else history[-1]
                                snap.liquidation_1h_long = float(completed.get("l", 0))
                                snap.liquidation_1h_short = float(completed.get("s", 0))
                                total = snap.liquidation_1h_long + snap.liquidation_1h_short
                                if total > 0:
                                    snap.liquidation_ratio = snap.liquidation_1h_long / total
                                filled = True
                except Exception as e:
                    self._throttled_warning(f"coinalyze_liq:{snap.symbol}", f"[DerivAnalytics] Coinalyze 清算数据异常: {e}")

                # 多空比补充（如果 Binance 没拿到）
                if snap.long_short_ratio == 1.0:
                    try:
                        # [2026-07-10 修复] 原用未定义变量 hour_ago → NameError 被
                        # 下方 except: pass 吞掉，Coinalyze 多空比兜底永远失效。
                        # 改用本函数定义的 four_hours_ago（与清算数据同窗口）。
                        r = client.get(f"{COINALYZE_BASE}/long-short-ratio-history",
                                       params={"symbols": ca_symbol, "interval": "1hour",
                                               "from": four_hours_ago, "to": now_ts},
                                       headers=headers)
                        if r.status_code == 200:
                            data = r.json()
                            if data and isinstance(data, list) and data[0].get("history"):
                                latest = data[0]["history"][-1]
                                snap.long_short_ratio = float(latest.get("r", 1))
                                filled = True
                    except Exception:
                        pass

                # OI 补充
                if snap.oi_total == 0:
                    try:
                        r = client.get(f"{COINALYZE_BASE}/open-interest",
                                       params={"symbols": ca_symbol, "convert_to_usd": "true"},
                                       headers=headers)
                        if r.status_code == 200:
                            data = r.json()
                            if data and isinstance(data, list):
                                snap.oi_total = float(data[0].get("value", 0))
                                filled = True
                    except Exception:
                        pass

        except Exception as e:
            self._throttled_warning(f"coinalyze_api:{snap.symbol}", f"[DerivAnalytics] Coinalyze API 异常: {e}")
        return filled

    # ────────────── Layer 5: Coinglass（v6 2.3 数据源抽象层） ──────────────

    def _coinglass_available(self) -> bool:
        """Coinglass 是否可用：有任一 key（付费/免费）才启用（无 key 端点必失败）。"""
        try:
            from backend.services.data.data_provider import get_coinglass_provider
            return get_coinglass_provider().has_key
        except Exception:
            return False

    def _fill_from_coinglass(self, snap: DerivativesSnapshot) -> bool:
        """从 Coinglass（统一 DataProvider）补 funding / 清算。

        只填其他层没有的缺口字段：funding_rate==0 时补 funding；
        liquidation_1h 为 0 时补清算量（Coinalyze 未配 key 时唯一清算源）。
        每次调用记录到 DataQualityMonitor（链上链路健康卡）。
        """
        if not self._coinglass_available():
            return False
        filled = False
        try:
            from backend.services.data.data_provider import get_coinglass_provider
            from backend.services.data_quality_monitor import get_data_quality_monitor
            dq = get_data_quality_monitor()
            provider = get_coinglass_provider()

            # funding（其他层未拿到时补）
            if snap.funding_rate == 0:
                t0 = time.time()
                f = provider.fetch_funding(snap.symbol)
                dq.record_source_call(
                    "coinglass_funding", success=(f is not None),
                    latency_ms=(time.time() - t0) * 1000,
                    error="" if f is not None else provider.stats.last_error,
                )
                if f is not None:
                    snap.funding_rate = float(f)
                    filled = True

            # 清算（Coinalyze 缺 key 或未拿到时补）
            if snap.liquidation_1h_long + snap.liquidation_1h_short == 0:
                t0 = time.time()
                liq = provider.fetch_liquidation(snap.symbol)
                dq.record_source_call(
                    "coinglass_liquidation", success=(liq is not None),
                    latency_ms=(time.time() - t0) * 1000,
                    error="" if liq is not None else provider.stats.last_error,
                )
                if liq is not None and liq.total_usd > 0:
                    snap.liquidation_1h_long = liq.long_usd
                    snap.liquidation_1h_short = liq.short_usd
                    snap.liquidation_ratio = (
                        liq.long_usd / liq.total_usd if liq.total_usd > 0 else 1.0
                    )
                    filled = True
        except Exception as e:
            self._throttled_warning(
                f"coinglass:{snap.symbol}", f"[DerivAnalytics] Coinglass 数据异常: {e}")
        return filled

    # ════════════════════════════════════════════
    #  分析逻辑
    # ════════════════════════════════════════════

    def _analyze(self, snap: DerivativesSnapshot):
        """根据分析矩阵判定综合信号"""
        oi_dir = "up" if snap.oi_change_1h > 0.01 else ("down" if snap.oi_change_1h < -0.01 else "flat")
        price_dir = self._infer_price_direction(snap.symbol)
        funding_extreme = abs(snap.funding_rate) > 0.001

        for m_oi, m_price, m_fund, m_sig, m_text in ANALYSIS_MATRIX:
            if oi_dir == m_oi and price_dir == m_price and funding_extreme == m_fund:
                snap.signal = m_sig
                snap.interpretation = m_text
                break

        strength = 0.0
        if abs(snap.funding_rate) > 0.0005:
            strength += 0.3
        if abs(snap.oi_change_1h) > 0.02:
            strength += 0.3
        if snap.liquidation_1h_long + snap.liquidation_1h_short > 5_000_000:
            strength += 0.2
        if abs(snap.long_short_ratio - 1.0) > 0.3:
            strength += 0.2
        snap.signal_strength = min(1.0, strength)

        if not snap.interpretation:
            snap.interpretation = f"OI {oi_dir}, 价格 {price_dir}, Funding {'极端' if funding_extreme else '正常'}"

    def _infer_price_direction(self, symbol: str) -> str:
        """从 MarketAssetMetrics 推断近1小时价格方向"""
        try:
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import MarketAssetMetrics
            import time as _time

            db = MarketSessionLocal()
            try:
                now_ms = int(_time.time() * 1000)
                hour_ago_ms = now_ms - 3600_000

                latest = db.query(MarketAssetMetrics).filter(
                    MarketAssetMetrics.symbol == symbol,
                    MarketAssetMetrics.mark_price.isnot(None),
                ).order_by(MarketAssetMetrics.timestamp.desc()).first()

                hour_ago = db.query(MarketAssetMetrics).filter(
                    MarketAssetMetrics.symbol == symbol,
                    MarketAssetMetrics.timestamp <= hour_ago_ms,
                    MarketAssetMetrics.mark_price.isnot(None),
                ).order_by(MarketAssetMetrics.timestamp.desc()).first()

                if latest and hour_ago and float(hour_ago.mark_price) > 0:
                    change = (float(latest.mark_price) - float(hour_ago.mark_price)) / float(hour_ago.mark_price)
                    if change > 0.005:
                        return "up"
                    elif change < -0.005:
                        return "down"
            finally:
                db.close()
        except Exception:
            pass
        return "flat"

    # ════════════════════════════════════════════
    #  OI四象限 & 清算聚集区
    # ════════════════════════════════════════════

    def get_oi_regime(self, symbol: str = "BTC") -> Dict[str, Any]:
        """OI四象限分析：价格变化×OI变化联合判定趋势阶段"""
        snap = self.get_snapshot(symbol)
        price_dir = self._infer_price_direction(symbol)
        oi_change = snap.oi_change_1h

        oi_up = oi_change > 0.015
        oi_down = oi_change < -0.015

        if oi_up and price_dir == "up":
            quadrant, signal, desc = "long_buildup", "bullish", "多头建仓：价格和OI同步上升，趋势延续中"
            risk = "注意过热后的急跌风险"
        elif oi_up and price_dir == "down":
            quadrant, signal, desc = "short_buildup", "bearish", "空头建仓：OI增加但价格下跌，新空头入场"
            risk = "若价格快速反弹会触发空头清算"
        elif oi_down and price_dir == "up":
            quadrant, signal, desc = "short_covering", "neutral", "空头平仓：价格上涨伴随OI下降，弱势反弹"
            risk = "反弹可能不持久，OI未增说明新资金没入场"
        elif oi_down and price_dir == "down":
            quadrant, signal, desc = "long_unwinding", "neutral", "多头投降：价格和OI同步下降，去杠杆中"
            risk = "可能接近阶段底部，但不宜过早抄底"
        else:
            quadrant, signal, desc = "consolidation", "neutral", "震荡整理：OI和价格变化不显著"
            risk = "等待明确方向突破"

        return {
            "symbol": symbol, "quadrant": quadrant, "signal": signal,
            "description": desc, "risk_note": risk,
            "oi_change_1h": oi_change, "oi_total": snap.oi_total,
            "price_direction": price_dir, "funding_rate": snap.funding_rate,
            "data_sources": snap.data_sources,
        }

    def get_liquidation_clusters(
        self, symbol: str = "BTC", cached_only: bool = False
    ) -> Dict[str, Any]:
        """清算聚集区分析：多空清算分布及磁吸方向。

        cached_only=True 时只读缓存快照（get_cached_snapshot，绝不同步拉网络），
        缓存未就绪则返回 {} 让调用方按"无清算簇数据"降级——用于 scalp 等热路径。
        """
        if cached_only:
            snap = self.get_cached_snapshot(symbol)
            if snap is None:
                return {}
        else:
            snap = self.get_snapshot(symbol)

        liq_long = snap.liquidation_1h_long
        liq_short = snap.liquidation_1h_short
        total = liq_long + liq_short

        if total < 100_000:
            bias, bias_signal, desc, severity = "balanced", "neutral", "清算量极小，无显著磁吸", "low"
        elif liq_short > liq_long * 2.5:
            bias, bias_signal = "upward_magnet", "bullish"
            desc, severity = f"空头清算远超多头({liq_short/max(liq_long,1):.1f}x)，上方有强磁吸", "high"
        elif liq_short > liq_long * 1.5:
            bias, bias_signal = "upward_magnet", "bullish"
            desc, severity = f"空头清算偏多({liq_short/max(liq_long,1):.1f}x)，轻度上方磁吸", "medium"
        elif liq_long > liq_short * 2.5:
            bias, bias_signal = "downward_magnet", "bearish"
            desc, severity = f"多头清算远超空头({liq_long/max(liq_short,1):.1f}x)，下方有强磁吸", "high"
        elif liq_long > liq_short * 1.5:
            bias, bias_signal = "downward_magnet", "bearish"
            desc, severity = f"多头清算偏多({liq_long/max(liq_short,1):.1f}x)，轻度下方磁吸", "medium"
        else:
            bias, bias_signal, desc, severity = "balanced", "neutral", "多空清算大致均衡", "low"

        return {
            "symbol": symbol, "bias": bias, "signal": bias_signal,
            "description": desc, "severity": severity,
            "liquidation_long_1h": liq_long, "liquidation_short_1h": liq_short,
            "total_1h": total,
            "long_pct": round(liq_long / total * 100, 1) if total > 0 else 50,
            "short_pct": round(liq_short / total * 100, 1) if total > 0 else 50,
            "liquidation_ratio": snap.liquidation_ratio,
            "data_sources": snap.data_sources,
        }


derivatives_analytics = DerivativesAnalyticsService()
