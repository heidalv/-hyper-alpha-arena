"""
K线实时采集服务 - 每分钟定时采集当前K线数据
新交易对自动回补历史数据以支持技术分析
"""

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set

from backend.services.market_data_symbol_config import resolve_configured_symbols
from backend.services.symbol_normalizer import normalize_symbol

from .exchange_config import get_active_exchange
from .kline_cache_service import kline_cache
from .kline_collector_executor import get_kline_collector_executor, shutdown_kline_collector_executor
from .kline_collectors import ExchangeDataSourceFactory, ExchangeRateLimitError
from .kline_data_service import kline_service
from .klines_ws_publisher import broadcast_after_collection

logger = logging.getLogger(__name__)

# 数据中台整改：从"够跑当下"改为"全历史"
# 根因1：BACKFILL_DAYS 4 天不足以做因子研究。改为可配置全历史（0=从上市日起）。
#   实际回填天数由 _initial_backfill 内按品种上市日动态计算。
BACKFILL_DAYS = int(os.getenv("KLINE_BACKFILL_DAYS", "0"))  # 0=全历史(上市日起), N=近N天
# 根因5：MIN_CANDLES 门槛从 55(2.3天) 提高到全历史级别。
#   1h 至少要 1 年(8760根) 才算"充足"；1d 至少 2 年(730根)。
MIN_1H_CANDLES_REQUIRED = int(os.getenv("KLINE_MIN_1H_CANDLES", "8760"))   # 1 年
MIN_1D_CANDLES_REQUIRED = int(os.getenv("KLINE_MIN_1D_CANDLES", "730"))    # 2 年
MIN_1M_COVERAGE_PCT = 95.0  # 1m 数据低于此覆盖率时触发回填（80→95）


def get_quote_exchanges() -> List[str]:
    """K 线页跨所对比需要同步 1m 的交易所列表。"""
    raw = os.getenv("MARKET_DATA_QUOTE_EXCHANGES", "asterdex,binance,hyperliquid")
    exchanges: List[str] = []
    for item in raw.split(","):
        ex = item.strip().lower()
        if ex and ex not in exchanges:
            exchanges.append(ex)
    return exchanges or ["asterdex"]


# 缓存「有交易活动的交易所」，避免每分钟查库（30s TTL）
_trading_exchanges_cache: List[str] = []
_trading_exchanges_ts: float = 0.0


def get_trading_exchanges() -> List[str]:
    """返回所有有交易活动的交易所（有活跃自动交易账户的 selected_exchange）。

    这些交易所需要采集全周期（1m~1d），因为策略/分析依赖其完整行情。
    历史问题：只有 active_exchange（主交易所）采全周期，其他所仅 1m，
    导致第二个有交易的交易所（如 binance）长周期数据长期停滞、策略盲跑。
    """
    global _trading_exchanges_cache, _trading_exchanges_ts
    import time as _time
    now = _time.time()
    if _trading_exchanges_cache and now - _trading_exchanges_ts < 30:
        return _trading_exchanges_cache
    result: Set[str] = set()
    # 兜底：主交易所一定算
    try:
        active = (get_active_exchange() or "").lower()
        if active:
            result.add(active)
    except Exception:
        pass
    # 动态：查活跃自动交易账户的 selected_exchange
    try:
        from sqlalchemy import text as sa_text

        from backend.database.connection import SessionLocal
        with SessionLocal() as db:
            rows = db.execute(sa_text(
                "SELECT DISTINCT selected_exchange FROM accounts "
                "WHERE is_active='true' AND auto_trading_enabled='true' "
                "AND selected_exchange IS NOT NULL AND selected_exchange != ''"
            )).fetchall()
        for r in rows:
            ex = (str(r[0]) if r[0] else "").strip().lower()
            # 归一化别名
            if ex == "aster":
                ex = "asterdex"
            if ex:
                result.add(ex)
    except Exception as e:
        logger.debug(f"get_trading_exchanges DB query failed: {e}")
    _trading_exchanges_cache = sorted(result) if result else list(get_quote_exchanges())
    _trading_exchanges_ts = now
    return _trading_exchanges_cache


# 缓存「完整交易 universe」，避免每分钟重复查持仓/策略（30s TTL）
_trade_universe_cache: List[str] = []
_trade_universe_ts: float = 0.0


def get_research_priority_symbols(limit: int = 80) -> List[str]:
    """选币看板 / VIP 关注币 → 并入 P0，避免山寨只靠慢速 P1 轮转而过期。

    来源：近期待选 CoinSelectCandidate + AutoCoinSelection(injected) + 环境变量。
    """
    out: List[str] = []
    seen: Set[str] = set()

    def _add(sym: str) -> None:
        su = normalize_symbol(sym)
        if not su or su in seen:
            return
        seen.add(su)
        out.append(su)

    extra = os.getenv("KLINE_RESEARCH_SYMBOLS", "").strip()
    if extra:
        for part in extra.replace(";", ",").split(","):
            _add(part)

    try:
        from datetime import datetime, timedelta

        from backend.core.tenant import set_system_identity
        from backend.database.connection import SessionLocal

        set_system_identity()
        since = datetime.utcnow() - timedelta(days=7)
        with SessionLocal() as db:
            try:
                from backend.database.models import CoinSelectCandidate

                rows = (
                    db.query(CoinSelectCandidate.symbol)
                    .filter(CoinSelectCandidate.created_at >= since)
                    .order_by(CoinSelectCandidate.id.desc())
                    .limit(limit)
                    .all()
                )
                for (sym,) in rows:
                    _add(sym)
                    if len(out) >= limit:
                        return out
            except Exception:
                pass
            try:
                from backend.database.models import AutoCoinSelection

                rows = (
                    db.query(AutoCoinSelection.symbol)
                    .filter(
                        AutoCoinSelection.created_at >= since,
                        AutoCoinSelection.action == "injected",
                    )
                    .order_by(AutoCoinSelection.id.desc())
                    .limit(limit)
                    .all()
                )
                for (sym,) in rows:
                    _add(sym)
                    if len(out) >= limit:
                        return out
            except Exception:
                pass
    except Exception as e:
        logger.debug("get_research_priority_symbols: %s", e)
    return out


def get_volume_top_symbols(limit: int = 100) -> List[str]:
    """Asterdex 24h 成交额 TopN → 并入 P0，避免「只有 BTC/ETH/SOL 新鲜」。

    读 ticker poller 的 stats；若本进程尚未有快照则 ensure 一次。
    """
    limit = max(10, min(int(limit or 100), 300))
    try:
        from backend.services.asterdex_ticker_poller import asterdex_ticker_poller

        if len(asterdex_ticker_poller.get_all_stats()) < 20:
            try:
                asterdex_ticker_poller.ensure_snapshot(max_age_sec=60, fan_out=False)
            except Exception:
                pass
        stats = asterdex_ticker_poller.get_all_stats() or {}
        ranked = sorted(
            stats.items(),
            key=lambda kv: float((kv[1] or {}).get("quote_volume_24h") or 0),
            reverse=True,
        )
        out: List[str] = []
        for sym, _ in ranked:
            su = (sym or "").upper().strip()
            if su and su not in out:
                out.append(su)
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        logger.debug("get_volume_top_symbols: %s", e)
        return []


def get_trade_universe_symbols() -> List[str]:
    """返回 running 会话的完整交易 universe（含持仓 + active 策略的币）。

    采集名单必须是交易名单的超集，否则出现「在交易但没数据」：
    `resolve_configured_symbols` 只读 session.symbols + auto_coin_symbols，
    而实际扫币走 `_resolve_session_trade_symbols`，后者还并入当前持仓和
    active 策略的 primary_symbol。AI 选币每 30 分钟轮换，被轮出的币会从
    auto_coin_symbols 消失（K线随即停采），但只要它还有 active 策略就仍在
    被扫描和下单——于是策略拿着冻结的旧K线做决策，其信号也永远无法结算。
    """
    global _trade_universe_cache, _trade_universe_ts
    import time as _time
    now = _time.time()
    if _trade_universe_cache and now - _trade_universe_ts < 30:
        return _trade_universe_cache
    result: List[str] = []
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession
        from backend.services.session_symbols import resolve_session_trade_symbols

        svc = None
        try:
            from backend.services.full_auto_trading_service import full_auto_service
            svc = full_auto_service
        except Exception:
            svc = None

        with SessionLocal() as db:
            # [2026-08-07 活跃口径对齐] 与 _active_trading_symbols() 一致：
            # running/defensive/paused 会话 + open 持仓，全部并入 P0 名单。
            sessions = db.query(FullAutoSession).filter(
                FullAutoSession.status.in_(["running", "defensive", "paused"])
            ).all()
            seen: Set[str] = set()
            for sess in sessions:
                for sym in (resolve_session_trade_symbols(sess, db, full_auto_service=svc) or []):
                    su = (sym or "").upper().strip()
                    if su and su not in seen:
                        seen.add(su)
                        result.append(su)
            # 直接并入 open 持仓（防御：会话币列表与持仓可能不一致，
            # 持仓币必须保持 K 线/价格实时，否则 stale 闸门拒绝交易链路）
            try:
                from backend.database.models import PaperPosition
                for pos in db.query(PaperPosition).filter(PaperPosition.status == "open").all():
                    su = normalize_symbol(str(getattr(pos, "symbol", "") or ""))
                    if su and su not in seen:
                        seen.add(su)
                        result.append(su)
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"get_trade_universe_symbols DB query failed: {e}")
    # 并入选币/研究优先级币，缩短山寨 P1 轮转等待
    try:
        for sym in get_research_priority_symbols(80):
            if sym not in result:
                result.append(sym)
    except Exception:
        pass
    if result:
        _trade_universe_cache = result
        _trade_universe_ts = now
    return _trade_universe_cache


class KlineRealtimeCollector:
    """K线实时采集服务"""

    def __init__(self):
        self.running = False
        self.collection_task = None
        self.gap_detection_task = None
        self.backfill_task = None
        self.p1_task = None
        # 专用后台线程 + 独立事件循环，与 uvicorn API 循环隔离
        self._collector_thread: Optional[threading.Thread] = None
        self._collector_loop: Optional[asyncio.AbstractEventLoop] = None
        self._started_event = threading.Event()
        self._p0_ready = threading.Event()  # P0 至少跑过一轮后再让 P2 重活

        # 用户配置的交易对是唯一默认来源；没有配置时跳过采集。
        self.default_symbols = []

        # 采集的 K 线周期（全周期），按分钟节流在 _periods_due_now 里决定何时真正拉取
        # 长周期不需要每分钟都拉：
        #   4h  —— 每 15 分钟刷一次（4h K 线 4 小时才结算一根，15 分钟刷足以不落后）
        #   1d  —— 整点 (minute==0) 刷一次即可
        #   1w  —— 每日 0 点刷一次（周线一根/周，日更足够）
        #   1M  —— 月线一根/月，随 P1 低频组轮转即可（无需每日刷）
        # [2026-08-10 修复] 全周期纳入 1M 月线：此前 asterdex 主所从未采集月线
        #（长线分析缺月线锚，用户反馈数据不全）。
        self.periods = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"]
        # 短周期全深度回填只针对日内周期；1w/1M 由 P1 浅补 + P2 深历史覆盖
        self._short_backfill_periods = [p for p in self.periods if p not in ("1w", "1M")]
        # P1 多交易所：每所独立 catalog / 游标 / 刷新时间
        self._p1_cursors: dict = {}
        self._p1_catalogs: dict = {}  # ex -> List[str]
        self._p1_catalog_ts: dict = {}  # ex -> float
        self._p1_ex_index = 0
        self._p0_busy = False
        self._last_active_exchange = ""
        self._p1_write_sem: Optional[asyncio.Semaphore] = None
        # [2026-08-04 修复] 最近一次命中交易所限流（429/418）的时间戳。
        # 命中后 P0/P1 在冷却窗口内跳过，避免每分钟 333+ 请求持续顶满
        # Asterdex 2400 req/min 滑动窗口造成"429→下轮继续撞墙"死循环。
        self._rate_limited_ts: float = 0.0
        # [2026-08-04 修复] 限流冷却时长（秒）：429 后全链路暂停足够久让
        # Asterdex 2400 req/min 滑动窗口完全回收，再以低速率恢复。
        # [2026-08-15 口径统一] 默认值与 _AsterdexRateLimiter._ban_backoff（90s）
        # 对齐——此前这里 120s、限速器 90s，两处不一致会让「本地跳过」与
        # 「全局封禁」的恢复时刻错开，形成交替撞墙窗口。
        self._rate_backoff_sec = float(os.getenv("ASTERDEX_RATE_BACKOFF_SEC", "90"))
        # [2026-08-04 修复] 连接级熔断：Asterdex 会「间歇性拒连」（SSL
        # UNEXPECTED_EOF / Connection reset，非 429），表现为 P0 整轮
        # 0ok/Nerr 且反复出现。检测到连续 N 轮全失败 → 暂停采集一段
        # 恢复期，让交易所网络层喘息后自动升速，避免每分钟 450 请求
        # 持续撞墙刷屏。
        self._conn_fail_streak: int = 0
        self._conn_circuit_until: float = 0.0
        self._conn_circuit_backoff = float(os.getenv("KLINE_CONN_CIRCUIT_BACKOFF_SEC", "180"))
        self._conn_circuit_threshold = max(1, int(os.getenv("KLINE_CONN_CIRCUIT_THRESHOLD", "2")))
        # P1 周期轮转游标：全周期分片采，避免单批超时，但保证全覆盖
        self._p1_period_group_index: dict = {}

    async def start(self):
        """启动实时采集服务（专用后台线程，不占用 API 事件循环）"""
        if self.running:
            logger.warning("Realtime collector is already running")
            return

        try:
            self.running = True
            self._started_event.clear()
            get_kline_collector_executor()  # 预热专用线程池

            self._collector_thread = threading.Thread(
                target=self._collector_thread_main,
                name="kline-realtime-collector",
                daemon=True,
            )
            self._collector_thread.start()

            if not self._started_event.wait(timeout=60):
                self.running = False
                raise RuntimeError("K-line collector thread failed to start within 60s")

            logger.info(
                "K-line realtime collector started (dedicated thread + isolated executor)"
            )

        except Exception as e:
            logger.error(f"Failed to start realtime collector: {e}")
            self.running = False
            raise

    def _collector_thread_main(self) -> None:
        """采集器主线程：独立 asyncio 事件循环，与 API 完全隔离。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._collector_loop = loop
        try:
            loop.run_until_complete(self._collector_async_main())
        except Exception as e:
            logger.error(f"K-line collector thread crashed: {e}", exc_info=True)
        finally:
            self.running = False
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            loop.close()
            self._collector_loop = None
            logger.info("K-line collector thread exited")

    async def _collector_async_main(self) -> None:
        """在专用循环内运行 P0 热路径 / P1 全量 / P2 回补与缺口检测。"""
        await kline_service.initialize()
        logger.info(
            "Starting K-line sync pools (P0 hot / P1 full-market / P2 backfill) "
            "on dedicated event loop"
        )

        self.collection_task = asyncio.create_task(self._realtime_collection_loop())
        self.p1_task = asyncio.create_task(self._p1_full_market_loop())
        self.watch_task = asyncio.create_task(self._p1_watch_loop())
        self.gap_detection_task = asyncio.create_task(self._gap_detection_loop())
        # [2026-08-04 修复] 双 P2 深回填去重：KLINE_DEPTH_BACKFILL_ENABLED=true 时
        # 由 kline_history_sync.DepthBackfillRunner 承担全 catalog 深回填（独立线程、
        # 每 6 小时补差、走 backfill 桶 150/min）。本采集器内嵌 _initial_backfill
        # 再跑一份会与 DepthBackfillRunner 竞争 backfill 桶 → 合并请求突破
        # Asterdex 软限流触发全链 429（实测 19:19-19:22 P2-Backfill 与 DepthBackfill
        # 并发，P0 长周期窗口 114err + 35s 冷却）。故二者互斥。
        _p2_depth_enabled = os.getenv(
            "KLINE_DEPTH_BACKFILL_ENABLED", "false"
        ).strip().lower() in ("1", "true", "yes", "on")
        if _p2_depth_enabled:
            self.backfill_task = asyncio.create_task(asyncio.sleep(0))
            logger.info("[P2-Backfill] KLINE_DEPTH_BACKFILL_ENABLED=true，由 DepthBackfillRunner 承担深回填，跳过内嵌 initial_backfill")
        else:
            self.backfill_task = asyncio.create_task(self._initial_backfill())
        self._started_event.set()

        await asyncio.gather(
            self.collection_task,
            self.p1_task,
            self.gap_detection_task,
            self.backfill_task,
            return_exceptions=True,
        )

    async def stop(self):
        """停止实时采集服务"""
        if not self.running and not self._collector_thread:
            return

        logger.info("Stopping K-line realtime collection service")
        self.running = False

        loop = self._collector_loop
        if loop and loop.is_running():
            def _cancel_tasks() -> None:
                for task in (
                    self.collection_task,
                    self.p1_task,
                    self.gap_detection_task,
                    self.backfill_task,
                ):
                    if task and not task.done():
                        task.cancel()

            loop.call_soon_threadsafe(_cancel_tasks)

        if self._collector_thread and self._collector_thread.is_alive():
            await asyncio.to_thread(self._collector_thread.join, 15)

        shutdown_kline_collector_executor()
        self._collector_thread = None
        logger.info("K-line realtime collector stopped")

    async def _realtime_collection_loop(self):
        """实时采集循环 - 每分钟整点执行"""
        logger.info("Starting realtime collection loop")

        while self.running:
            try:
                # 等待到下一个整分钟
                await self._wait_for_next_minute()

                if not self.running:
                    break

                # 执行采集
                await self._collect_current_minute()

            except asyncio.CancelledError:
                logger.info("Realtime collection loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in realtime collection loop: {e}")
                # 出错后等待30秒再继续
                await asyncio.sleep(30)

    async def _wait_for_next_minute(self):
        """等待到下一个整分钟（XX:00.0），并补偿 sleep 计时漂移。

        历史 bug：原计算 60-second-microsecond/1e6，因 asyncio.sleep 不精确 +
        循环处理延迟，实际总落在 XX:59.x，比整点早约 0.x~1 秒。
        导致 _periods_due_now 取到的 minute 比真实整点小 1（如 44 而非 45），
        永远错过 %5==0 的时刻 → 15m/30m/1h/4h/1d 长周期从不采集，
        binance/asterdex 长周期数据长期停滞。
        修复：sleep 到整点后，额外 sleep 一小段并校验已跨入新分钟。
        """
        now = datetime.now()
        seconds_to_wait = 60 - now.second - now.microsecond / 1_000_000
        if seconds_to_wait < 1:
            seconds_to_wait += 60
        # 多等 0.5s 缓冲，确保 wake 时已真正跨入新分钟（second 接近 0）
        seconds_to_wait += 0.5
        await asyncio.sleep(seconds_to_wait)
        # 防御：若仍落在上一分钟（漂移），补等到下个整点
        for _ in range(3):
            cur = datetime.now()
            if cur.second >= 0 and cur.microsecond < 900_000:
                # 已在新分钟的 0.x 秒内
                break
            await asyncio.sleep(0.3)

    def _periods_due_now(self, current_time: datetime) -> List[str]:
        """按分钟节流决定当前这一轮要采集哪些周期。

        规则（[2026-08-06 重构]）：
        - P0 只采短周期（1m/3m/5m）：每分钟全量。
          此前把 15m/30m/1h/4h 长周期错峰拉进 P0，在 113+ symbols 下
          每轮 456-460 任务，12 并发 × 70s 预算必然全量超时（8-06 实测
          ~75% 轮次 0ok，长周期轮 100% 超时，触发 180s 熔断死循环）。
        - 长周期（15m/30m/1h/4h/1d/1w）全部由 P1 full-period 轮转覆盖
          （asterdex 每 12 轮中 2 轮 + 冷所轮转），P2 深度回补补齐历史。
        """
        return [p for p in ("1m", "3m", "5m") if p in self.periods]

    def _build_p0_symbols(self) -> List[str]:
        """P0 热币 = 配置 ∪ 交易 universe ∪ 成交额 TopN（补山寨新鲜度）。"""
        symbols = []
        try:
            symbols = kline_service.get_supported_symbols() or []
        except Exception:
            symbols = []
        cfg_symbols, _ = resolve_configured_symbols("KLINE_REALTIME_SYMBOLS")
        universe_symbols = get_trade_universe_symbols()
        volume_top: List[str] = []
        try:
            top_n = int(os.getenv("KLINE_P0_VOLUME_TOP", "100"))
            volume_top = get_volume_top_symbols(top_n)
        except Exception:
            volume_top = []
        merged: List[str] = []
        for s in list(symbols) + list(cfg_symbols or []) + list(universe_symbols or []) + list(volume_top or []):
            su = normalize_symbol(s)
            if su and su not in merged:
                merged.append(su)
        # 用 asterdex 全市场 ticker 表过滤：剔除该交易所不支持的币（如 KPEPE），
        # 避免每轮 P0 反复报 "does not have market symbol"。
        try:
            active_ex = (get_active_exchange() or "asterdex").strip().lower()
            if active_ex == "aster":
                active_ex = "asterdex"
            if active_ex == "asterdex":
                from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
                supported = set(asterdex_ticker_poller.get_all_prices().keys())
                if len(supported) < 20:
                    try:
                        asterdex_ticker_poller.ensure_snapshot(max_age_sec=60, fan_out=False)
                        supported = set(asterdex_ticker_poller.get_all_prices().keys())
                    except Exception:
                        pass
                if supported:
                    merged = [s for s in merged if s in supported]
        except Exception:
            pass
        # 防止 P0 膨胀拖垮分钟级超时：软上限
        soft_cap = max(40, int(os.getenv("KLINE_P0_SYMBOL_CAP", "120")))
        if len(merged) > soft_cap:
            merged = merged[:soft_cap]
        return merged

    def _p1_pick_exchange(self) -> str:
        """加权轮转：默认所（asterdex）多刷几轮，其它所降频。

        KLINE_P1_ACTIVE_WEIGHT=4 → 每 4+N-1 次里有 4 次刷 active。
        KLINE_P1_COLD_WEIGHT（默认 2）：非 active 所（binance/okx/bybit/hyperliquid）
        的权重，提高后多所全周期补齐更快（[2026-08-04] bybit/okx 曾长期只有近端
        数据，主要因轮转权重过低；冷所请求不过 Asterdex 限流桶，提高权重安全）。
        """
        exchanges = self._p1_sync_exchanges()
        if not exchanges:
            return "asterdex"
        active = exchanges[0]
        try:
            weight = max(1, int(os.getenv("KLINE_P1_ACTIVE_WEIGHT", "4")))
            cold = max(1, int(os.getenv("KLINE_P1_COLD_WEIGHT", "2")))
        except Exception:
            weight, cold = 4, 2
        weighted: List[str] = []
        for ex in exchanges:
            w = weight if ex == active else cold
            weighted.extend([ex] * w)
        idx = int(getattr(self, "_p1_ex_index", 0) or 0) % len(weighted)
        self._p1_ex_index = idx + 1
        return weighted[idx]
    async def _collect_current_minute(self):
        """P0 决策池：仅当前 active_exchange × 热币，独占超时预算。

        2026-07-31 阶段1：禁止再把 HL/BN 等报价所与热路径混跑——
        跨所全量归 P1；历史回填归 P2。
        """
        current_time = datetime.now()
        symbols = self._build_p0_symbols()
        if not symbols:
            logger.info("[P0] Skipping: no hot symbols")
            return

        # [2026-08-04 修复] 限流冷却窗口：上一轮命中 429/418 后暂停 60s，
        # 让 Asterdex 的滑动窗口先回收配额，否则 P0 每分钟 333 请求会持续
        # 撞墙（实测 16:35 触发 429 后连续 3 轮 P0 全失败）。
        _now_ts = time.time()
        if _now_ts - self._rate_limited_ts < self._rate_backoff_sec:
            logger.warning(
                "[P0] 交易所限流冷却中（%.0fs 后恢复），本轮跳过 %d symbols",
                self._rate_backoff_sec - (_now_ts - self._rate_limited_ts), len(symbols),
            )
            return

        # [2026-08-04 修复] 连接级熔断：连续全失败（SSL EOF/拒连特征）时
        # 暂停整轮采集一段恢复期，防止每分钟 450 请求持续撞墙刷屏。
        if _now_ts < self._conn_circuit_until:
            logger.warning(
                "[P0] 连接熔断恢复中（%.0fs 后重启），本轮跳过 %d symbols "
                "(连续 %d 轮全失败)",
                self._conn_circuit_until - _now_ts,
                len(symbols),
                self._conn_fail_streak,
            )
            return

        try:
            active_ex = (get_active_exchange() or "asterdex").strip().lower()
        except Exception:
            active_ex = "asterdex"
        if active_ex == "aster":
            active_ex = "asterdex"

        # 切换交易所：热币立刻插队到新所 P1 队首，加速冷仓数据就绪
        if self._last_active_exchange and self._last_active_exchange != active_ex:
            logger.warning(
                f"[P0] active_exchange 切换 {self._last_active_exchange} → {active_ex}，"
                f"热币插队 P1 ({len(symbols)} symbols)"
            )
            self._promote_hot_symbols(active_ex, symbols)
        self._last_active_exchange = active_ex

        periods_now = self._periods_due_now(current_time)
        logger.info(
            f"[P0] Collecting at {current_time.strftime('%Y-%m-%d %H:%M:%S')}, "
            f"exchange={active_ex}, periods={periods_now}, "
            f"symbols={len(symbols)}, sample={symbols[:12]}"
        )

        sem = asyncio.Semaphore(int(os.getenv("KLINE_P0_CONCURRENCY", "24")))
        # [2026-08-04 修复] 长周期错峰全量后瞬时任务 ≤448（短336+单个长112），
        # 并发16实测 448 任务 70s 超时（0ok/448err）；并发提到 24，
        # 448/24≈19批×~2.8s≈53s，能在 70s 预算内完成。
        P0_TIMEOUT_S = int(os.getenv("KLINE_P0_TIMEOUT_S", "70"))
        self._p0_busy = True
        try:
            async def _limited(symbol, period):
                async with sem:
                    return await self._collect_symbol_kline(symbol, period, exchange_id=active_ex)

            # [2026-08-04 修复] 长周期错峰全量后每个槽位至多 1 个长周期
            # （见 _periods_due_now），不再需要 (minute//5)%3 分片削峰。
            # 全量采集保证 15m/30m/1h 每个 symbol 每 5 分钟更新一次，
            # 15m 图滞后从 ~27 分钟降到 ~5 分钟。
            tasks = []
            for symbol in symbols:
                for period in periods_now:
                    tasks.append(_limited(symbol, period))

            ok, err = 0, 0
            if tasks:
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=P0_TIMEOUT_S,
                    )
                    ok = sum(1 for r in results if r is True)
                    err = len(results) - ok
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[P0] timed out after {P0_TIMEOUT_S}s "
                        f"(tasks={len(tasks)}, exchange={active_ex})"
                    )
                    err = len(tasks)
        finally:
            self._p0_busy = False

        logger.info(
            f"[P0] done: {ok}ok/{err}err "
            f"(symbols={len(symbols)}, periods={periods_now}, exchange={active_ex})"
        )
        # [2026-08-04 修复] 连接熔断判定：整轮 0 成功且失败>0（且非 429 冷却态），
        # 视为 Asterdex 间歇性拒连（SSL EOF）。连续达到阈值 → 进入熔断窗口，
        # 暂停采集让网络层恢复；任一轮有成功则重置计数（自动升速）。
        if ok == 0 and err > 0 and (_now_ts - self._rate_limited_ts) >= self._rate_backoff_sec:
            self._conn_fail_streak += 1
            if self._conn_fail_streak >= self._conn_circuit_threshold:
                self._conn_circuit_until = _now_ts + self._conn_circuit_backoff
                logger.warning(
                    "[P0] 检测到连续 %d 轮全失败（交易所拒连特征），"
                    "进入 %.0fs 连接熔断恢复期",
                    self._conn_fail_streak,
                    self._conn_circuit_backoff,
                )
                self._conn_fail_streak = 0
        else:
            if self._conn_fail_streak:
                logger.info(
                    "[P0] 采集恢复，连接失败计数清零 (was %d)", self._conn_fail_streak
                )
            self._conn_fail_streak = 0
        try:
            from backend.services.kline_sync_meta import record_heartbeat
            record_heartbeat(
                active_ex,
                pool="p0",
                period="*",
                symbols_ok=ok,
                symbols_fail=err,
                meta={
                    "symbols": len(symbols),
                    "periods": periods_now,
                    "sample": symbols[:8],
                },
            )
        except Exception:
            pass
        # [v6 2.3] 行情/K线链路健康记录：P0 每轮成功/失败写入 DataQualityMonitor
        try:
            from backend.services.data_quality_monitor import get_data_quality_monitor
            get_data_quality_monitor().record_source_call(
                f"kline_p0_{active_ex}", success=(err == 0),
                latency_ms=round((time.time() - _now_ts) * 1000, 1),
                error=f"{err} err" if err else "",
            )
        except Exception:
            pass
        self._p0_ready.set()

    def _p1_sync_exchanges(self) -> List[str]:
        """四所全量名单（可 env 覆盖）。active_exchange 排最前。"""
        raw = os.getenv("KLINE_SYNC_EXCHANGES", "").strip()
        if raw:
            exchanges = [x.strip().lower() for x in raw.split(",") if x.strip()]
        else:
            try:
                from config import settings
                exchanges = list(getattr(settings, "KLINE_SYNC_EXCHANGES", None) or [])
            except Exception:
                exchanges = []
        if not exchanges:
            exchanges = ["asterdex", "binance", "okx", "hyperliquid"]
        normalized: List[str] = []
        for ex in exchanges:
            if ex == "aster":
                ex = "asterdex"
            if ex and ex not in normalized:
                normalized.append(ex)
        try:
            active = (get_active_exchange() or "").strip().lower()
            if active == "aster":
                active = "asterdex"
            if active and active in normalized:
                normalized = [active] + [x for x in normalized if x != active]
            elif active and active not in normalized:
                normalized = [active] + normalized
        except Exception:
            pass
        return normalized

    def _promote_hot_symbols(self, exchange: str, symbols: List[str]) -> None:
        """把热币放到指定所 P1 队列最前（切换交易所后分钟级追上）。"""
        if not symbols:
            return
        catalog = list(self._p1_catalogs.get(exchange) or [])
        hot = []
        seen = set()
        for s in symbols:
            su = (s or "").upper().strip()
            if su and su not in seen:
                seen.add(su)
                hot.append(su)
        rest = [s for s in catalog if s not in seen]
        self._p1_catalogs[exchange] = hot + rest
        self._p1_cursors[exchange] = 0
        logger.info(
            f"[P1] promote hot→front @{exchange}: {len(hot)} symbols, catalog={len(hot)+len(rest)}"
        )

    async def _p1_full_market_loop(self):
        """P1：四所全市场增量轮转，与 P0 隔离。

        每轮选一个交易所采一小批；active_exchange 优先；P0 忙碌时降并发背压。
        """
        enabled = os.getenv("KLINE_P1_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off",
        )
        if not enabled:
            logger.info("[P1] disabled by KLINE_P1_ENABLED")
            return

        interval_s = max(20, int(os.getenv("KLINE_P1_INTERVAL_S", "60")))
        exchanges = self._p1_sync_exchanges()
        logger.info(
            f"[P1] full-market loop started (interval={interval_s}s, exchanges={exchanges})"
        )
        await asyncio.sleep(15)  # 让 P0 先占资源

        # 启动时预热各所 catalog（失败不阻断）
        for ex in exchanges:
            try:
                self._refresh_p1_catalog(ex, force=True)
            except Exception as e:
                logger.warning(f"[P1] startup catalog {ex} failed: {e}")

        while self.running:
            try:
                # [2026-08-04 修复] P0 采集期间完全让出（不再 20s 超时强跑），
                # 避免 P1 asterdex 批次与 P0 同时抢 900/min 配额触发全链冷却。
                # 180s 上限覆盖 P0 长周期窗口（444 请求约 90s + 余量），
                # 确保 P1 批次在 P0 完成后才开始，杜绝并发抢配额。
                waited = 0
                while self._p0_busy and self.running and waited < 180:
                    await asyncio.sleep(1)
                    waited += 1
                exchanges = self._p1_sync_exchanges()
                if not exchanges:
                    await asyncio.sleep(interval_s)
                    continue
                ex = self._p1_pick_exchange()
                await self._p1_collect_batch(ex)
            except asyncio.CancelledError:
                logger.info("[P1] cancelled")
                break
            except Exception as e:
                logger.error(f"[P1] error: {e}", exc_info=True)
            await asyncio.sleep(interval_s)

    async def _p1_watch_loop(self):
        """[2026-08-06] 观察币独立高频刷新：KlineFreshness 巡检币不受 P1 组轮转拖累。

        问题：P1 每轮只选一所采一批×一组周期，观察币（每批置前）的刷新间隔 =
        单所批间隔 × 组数，实测 12-25 分钟（binance 6min 靠运气、hyperliquid 21min
        常态 + 429），超过 1m critical 阈值（10min），巡检 critical 无法归零。
        本循环每 KLINE_P1_WATCH_INTERVAL_S（默认 240s）对全所观察币刷全周期，
        请求量小（6 symbols × 6 periods × 4 所 ≈ 170/轮），观察币 freshness 收敛到
        分钟级。active 所跳过 1m/3m/5m（P0 已每分钟覆盖）。
        """
        enabled = os.getenv("KLINE_P1_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off",
        )
        if not enabled:
            return
        interval_s = max(30, int(os.getenv("KLINE_P1_WATCH_INTERVAL_S", "240")))
        logger.info(f"[P1-Watch] freshness watch loop started (interval={interval_s}s)")
        await asyncio.sleep(20)  # 让 P0/P1 先占资源
        _long_periods = ("4h", "1d", "1w", "1M")  # 阈值宽（4h=8h critical），无需每轮刷
        round_no = 0
        while self.running:
            round_no += 1
            try:
                exchanges = self._p1_sync_exchanges()
                watch = self._freshness_watch_symbols()
                if not exchanges or not watch:
                    await asyncio.sleep(interval_s)
                    continue
                try:
                    active = (get_active_exchange() or "").strip().lower()
                    if active == "aster":
                        active = "asterdex"
                except Exception:
                    active = ""
                all_periods = self._p1_all_periods()
                conc = max(2, int(os.getenv("KLINE_P1_WATCH_CONCURRENCY", "3")))
                sem = asyncio.Semaphore(conc)

                async def _limited(symbol, period, ex):
                    async with sem:
                        return await self._p1_sync_symbol_period(symbol, period, ex)

                t0 = time.time()
                ok = err = 0
                for ex in exchanges:
                    # [2026-08-11 修复] 活跃所观察币并入“交易宇宙 + 选币研究池”，
                    # 保证 auto_coin/持仓/active 策略币的 15m/30m/1h 分钟级刷新，
                    # 不再依赖 P1 游标轮转（519 币轮一圈要数小时）。
                    watch_ex = list(watch)
                    if ex == active:
                        try:
                            merged: List[str] = []
                            for s in (get_trade_universe_symbols() + get_research_priority_symbols(80)):
                                su = normalize_symbol(s)
                                if su and su not in merged:
                                    merged.append(su)
                            watch_ex = list(dict.fromkeys(watch_ex + merged))
                        except Exception as _we:
                            logger.debug(f"[P1-Watch] merge trade universe failed: {_we}")
                    try:
                        _cat = self._p1_catalogs.get(ex)
                        if _cat:
                            watch_ex = [s for s in watch_ex if s in _cat]
                    except Exception:
                        pass
                    try:
                        _max_watch = int(os.getenv(
                            "KLINE_P1_WATCH_MAX_SYMBOLS",
                            "60" if ex == active else "12",
                        ))
                    except Exception:
                        _max_watch = 60 if ex == active else 12
                    watch_ex = watch_ex[:_max_watch]
                    periods_for_ex = all_periods
                    if ex == active:
                        # active 所 1m/3m/5m 由 P0 每分钟覆盖，不重复请求
                        periods_for_ex = [p for p in all_periods if p not in ("1m", "3m", "5m")]
                    if round_no % 4 != 0:
                        # 长周期阈值宽（4h critical=8h/1d=48h），每 4 轮刷一次即可；
                        # 每轮全刷会放大 hl/okx 429 压力（实测 watch err 稳定 50-63/轮）
                        periods_for_ex = [p for p in periods_for_ex if p not in _long_periods]
                    tasks = [
                        asyncio.create_task(_limited(s, p, ex))
                        for s in watch_ex
                        for p in periods_for_ex
                    ]
                    if not tasks:
                        continue
                    # 每所独立 60s 上限：单所 429 重试（每请求 2-6s）不拖垮整轮，
                    # 失败项下轮补刷（观察币另有 P1 主循环组轮转双通道覆盖）
                    done, pending = await asyncio.wait(tasks, timeout=60)
                    for t in done:
                        try:
                            if t.result() is True:
                                ok += 1
                            else:
                                err += 1
                        except Exception:
                            # 观察币 429/网络失败：本轮跳过，不触发全链冷却
                            err += 1
                    for t in pending:
                        t.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                        err += len(pending)
                logger.info(
                    f"[P1-Watch] done ok={ok} err={err} elapsed={time.time() - t0:.0f}s "
                    f"symbols={watch_ex} exchanges={exchanges}"
                )
            except asyncio.CancelledError:
                logger.info("[P1-Watch] cancelled")
                break
            except Exception as e:
                logger.error(f"[P1-Watch] error: {e}")
            await asyncio.sleep(interval_s)

    def _refresh_p1_catalog(self, exchange: str, force: bool = False) -> List[str]:
        import time as _time
        now = _time.time()
        ttl = int(os.getenv("KLINE_P1_CATALOG_TTL_S", "1800"))
        cached = self._p1_catalogs.get(exchange) or []
        ts = float(self._p1_catalog_ts.get(exchange) or 0)
        if cached and not force and (now - ts) < ttl:
            return cached
        try:
            from backend.services.kline_sync_meta import (
                list_catalog_symbols,
                refresh_catalog_from_scanner,
            )
            symbols = refresh_catalog_from_scanner(exchange)
            if not symbols:
                symbols = list_catalog_symbols(exchange)
            self._p1_catalogs[exchange] = symbols or []
            self._p1_catalog_ts[exchange] = now
            if exchange not in self._p1_cursors:
                self._p1_cursors[exchange] = 0
        except Exception as e:
            logger.warning(f"[P1] catalog refresh failed @{exchange}: {e}")
        return self._p1_catalogs.get(exchange) or []

    def _freshness_watch_symbols(self) -> List[str]:
        """KlineFreshness 巡检观察币：P1 每批强制优先刷新。

        [2026-08-06 修复] P1 字母序 cursor 轮转下，冷所（binance/hyperliquid）排序
        为 cold + hot_in_cat，P0 热币排 catalog 尾部，观察币刷新间隔长达 1.5-8 小时
        （8-06 实测 binance 1m stale 8-16h、asterdex 15m stale 82min），
        KlineFreshness critical 恒 19/108。观察币置每批头部即可保证：
        active 所每批都刷（分钟级），冷所每轮到一次刷一次（10-20 分钟级）。
        """
        raw = os.getenv("KLINE_FRESHNESS_SYMBOLS", "") or os.getenv("MARKET_DATA_V2_SYMBOLS", "")
        if not raw or raw == "account_selected":
            raw = "BTC,ETH,SOL,BNB,ASTER,JTO"
        out: List[str] = []
        for s in raw.split(","):
            su = normalize_symbol(s)
            if su and su not in out:
                out.append(su)
        return out

    def _p1_all_periods(self) -> List[str]:
        """仓储全量周期（不是短线三档）。可用 KLINE_P1_PERIODS 覆盖。"""
        raw = os.getenv(
            "KLINE_P1_PERIODS",
            "1m,3m,5m,15m,30m,1h,4h,1d,1w,1M",
        )
        periods = [p.strip() for p in raw.split(",") if p.strip()]
        return periods or list(self.periods)

    def _p1_period_groups(self) -> List[List[str]]:
        """把全周期拆成几组轮转，每批采一组，多轮后覆盖全部。

        组划分兼顾短/中/长，避免永远只刷 5m。
        """
        all_p = self._p1_all_periods()
        # 允许 env 一次全采：KLINE_P1_PERIOD_MODE=all
        mode = os.getenv("KLINE_P1_PERIOD_MODE", "rotate").strip().lower()
        if mode in ("all", "full", "once"):
            return [all_p]
        groups_def = [
            ["1m", "3m", "5m"],
            ["15m", "30m", "1h"],
            ["4h", "1d", "1w", "1M"],
        ]
        out: List[List[str]] = []
        for g in groups_def:
            hit = [p for p in g if p in all_p]
            if hit:
                out.append(hit)
        # 用户自定义周期若不在上面三组，单独一组
        known = {p for g in out for p in g}
        extra = [p for p in all_p if p not in known]
        if extra:
            out.append(extra)
        return out or [all_p]

    def _p1_depth_days(self, period: str) -> int:
        """P1 薄弱时只补「浅层」近端，深历史交给 P2，避免单批卡死。"""
        # [2026-08-10 修复] "1M" 月线与 "1m" 分钟周期 env 键同名（KLINE_P1_DEPTH_DAYS_1M
        # 已被 1m 占用，.env=30 天）→ 月线走专用键，避免误读 30 天（≈1 根月线）触发反复深补。
        if period == "1M":
            try:
                return max(1, int(os.getenv("KLINE_P1_DEPTH_DAYS_1M_MONTH", "1825")))
            except (TypeError, ValueError):
                return 1825
        env_key = f"KLINE_P1_DEPTH_DAYS_{period.upper()}"
        if os.getenv(env_key):
            try:
                return max(1, int(os.getenv(env_key, "0")))
            except Exception:
                pass
        # 浅层默认：保证有可用序列，但不在 P1 拉年级别
        defaults = {
            "1m": int(os.getenv("KLINE_P1_DEPTH_DAYS_1M", "1")),
            "3m": 2,
            "5m": 3,
            "15m": 7,
            "30m": 14,
            "1h": 30,
            "4h": 60,
            "1d": 180,
            "1w": 365,
            "1M": 1825,
        }
        return int(defaults.get(period, 7))

    def _p1_min_bars(self, period: str) -> int:
        """少于此根数视为薄弱。P1 默认阈值较低，深补由 P2 负责。"""
        if os.getenv("KLINE_P1_DEPTH_ENABLED", "false").strip().lower() in (
            "0", "false", "no", "off",
        ):
            return 10**9  # 永不触发 P1 深补，只写最新一根（深历史归 P2）
        defaults = {
            "1m": 200,
            "3m": 150,
            "5m": 150,
            "15m": 150,
            "30m": 120,
            "1h": 200,
            "4h": 120,
            "1d": 120,
            "1w": 40,
            "1M": 12,
        }
        override = os.getenv("KLINE_P1_MIN_BARS", "").strip()
        if override:
            try:
                return max(1, int(override))
            except Exception:
                pass
        return defaults.get(period, 100)

    def _count_klines(self, exchange: str, symbol: str, period: str) -> int:
        try:
            from sqlalchemy import text as sa_text
            from backend.database.connection import MarketSessionLocal
            with MarketSessionLocal() as db:
                n = db.execute(sa_text("""
                    SELECT COUNT(*) FROM crypto_klines
                    WHERE exchange = :ex AND symbol = :sym AND period = :p
                """), {"ex": exchange, "sym": symbol.upper(), "p": period}).scalar()
            return int(n or 0)
        except Exception:
            return 0

    async def _p1_sync_symbol_period(self, symbol: str, period: str, exchange_id: str) -> bool:
        """P1 单币单周期：可选浅层近端；默认以最新一根为主，深历史走 P2。

        [2026-08-04 修复] active 所（asterdex）刷新策略与 P0/P2 隔离：
        - 不做 P1 深补（深历史全交给 P2 深度回填，400 天覆盖已足够）；
        - 短周期（1m/3m/5m/15m/30m/1h）最新一根由 P0 每分钟覆盖，
          P1 重复刷新会与 P0 竞争 live 桶触发 429（实测 19:14 P0 整轮 78s 冷却）；
        - 长周期（4h/1d/1w）P0 不覆盖，P1 低频刷最新一根保持新鲜度。
        非 active 所无 P0/P2 兜底，维持深补 + 最新一根。
        """
        while self._p0_busy and self.running:
            await asyncio.sleep(0.5)
        if not self.running:
            return False

        try:
            active = (get_active_exchange() or "").strip().lower()
            if active == "aster":
                active = "asterdex"
        except Exception:
            active = ""

        if exchange_id == active:
            # [2026-08-06 修复] 此前 15m/30m/1h 也在此跳过（假设 P0 覆盖），
            # 但 dcA 后 P0 只采 1m/3m/5m → asterdex 15m/30m/1h 成真空区
            # （P0 不采、P1 跳过），巡检 critical 恒 5 项（8-06 实测 stale 82-107min
            # 且持续增长）。改为仅 1m/3m/5m 交给 P0，15m+ 由 P1 批次刷新。
            if period in ("1m", "3m", "5m"):
                return True
            return await self._collect_symbol_kline(symbol, period, exchange_id=exchange_id)

        min_bars = self._p1_min_bars(period)
        # 快速路径：绝大多数情况只刷最新一根，保证全周期覆盖速度
        if min_bars >= 10**9:
            return await self._collect_symbol_kline(symbol, period, exchange_id=exchange_id)

        existing = await asyncio.to_thread(self._count_klines, exchange_id, symbol, period)
        if existing < min_bars:
            try:
                collector = ExchangeDataSourceFactory.get_collector(exchange_id)
                end_time = datetime.now(timezone.utc)
                start_time = end_time - timedelta(days=self._p1_depth_days(period))

                async def _fetch():
                    return await collector.fetch_historical_klines(
                        symbol, start_time, end_time, period,
                    )

                klines = await asyncio.wait_for(_fetch(), timeout=25)
                if klines:
                    ok = await kline_service._insert_kline_data(klines)
                    if ok:
                        kline_cache.invalidate_cascade(symbol, period, exchange=exchange_id)
                        return True
            except ExchangeRateLimitError:
                # [2026-08-04 修复] 历史回填命中限流：交给 _collect_symbol_kline
                # 之外的冷却逻辑处理 —— 直接重抛由 _p1_collect_batch 兜底。
                raise
            except Exception as e:
                logger.debug(
                    f"[P1] shallow backfill fail {symbol}/{period}@{exchange_id}: {e}"
                )
        return await self._collect_symbol_kline(symbol, period, exchange_id=exchange_id)

    async def _p1_collect_batch(self, exchange: str | None = None):
        """一轮 P1：指定交易所一批 symbol × 一组全量周期（轮转覆盖 1m~1w）。

        仓储目标是四所全周期可用，不是只服务短线三档。
        薄弱币种会先拉近端历史深度，再维持增量。
        """
        # [2026-08-04 修复] 与 P0 共享限流冷却：429 窗口内跳过本轮，
        # 避免 P1 的批量请求继续顶满 Asterdex 滑动窗口。
        if time.time() - self._rate_limited_ts < self._rate_backoff_sec:
            logger.debug("[P1] 交易所限流冷却中，本轮跳过")
            return
        # [2026-08-04 修复] 共享连接熔断：Asterdex 间歇拒连期间 P1 也停，
        # 避免 P1 批次继续撞墙（与 P0 共用 _conn_circuit_until）。
        if time.time() < self._conn_circuit_until:
            logger.debug("[P1] 连接熔断恢复中，本轮跳过")
            return
        exchange = (exchange or self._p1_sync_exchanges()[0]).strip().lower()
        if exchange == "aster":
            exchange = "asterdex"
        # [2026-08-11 修复] 冷所（binance/okx/bybit/gateio）自己的冷却窗口：
        # 冷却期内整批跳过，不再对每个 symbol 逐个报错把一次限流放大成几十上百个失败。
        from backend.services.kline_collectors import _ColdExchangeRateLimiter
        if exchange != "asterdex" and _ColdExchangeRateLimiter.banned_remaining(exchange) > 0:
            logger.info(
                "[P1] %s 限流冷却中（%.0fs 后恢复），本轮整批跳过",
                exchange, _ColdExchangeRateLimiter.banned_remaining(exchange),
            )
            try:
                from backend.services.kline_sync_meta import record_heartbeat
                record_heartbeat(
                    exchange, pool="p1", period="*", symbols_ok=0, symbols_fail=0,
                    meta={"skipped": "rate_limit_cooldown"},
                )
            except Exception:
                pass
            return
        catalog = self._refresh_p1_catalog(exchange)
        if not catalog:
            logger.warning(f"[P1] empty catalog for {exchange}")
            try:
                from backend.services.kline_sync_meta import record_heartbeat
                record_heartbeat(
                    exchange, pool="p1", period="*", symbols_ok=0, symbols_fail=0,
                    meta={"error": "empty_catalog"},
                )
            except Exception:
                pass
            return

        hot = set(self._build_p0_symbols())
        try:
            active = (get_active_exchange() or "").strip().lower()
            if active == "aster":
                active = "asterdex"
        except Exception:
            active = ""
        cold = [s for s in catalog if s not in hot]
        hot_in_cat = [s for s in catalog if s in hot]
        if exchange == active:
            ordered = hot_in_cat + cold
        else:
            ordered = cold + hot_in_cat

        # 全周期后单批任务量更大 → 默认批次略减；默认所加大批次加速山寨追平
        batch_size = max(3, int(os.getenv("KLINE_P1_BATCH_SIZE", "10")))
        if exchange == active:
            try:
                active_batch = int(os.getenv("KLINE_P1_ACTIVE_BATCH_SIZE", "0") or "0")
            except Exception:
                active_batch = 0
            if active_batch > 0:
                batch_size = max(batch_size, active_batch)
            else:
                batch_size = max(batch_size, min(batch_size * 2, 50))
        cursor = int(self._p1_cursors.get(exchange) or 0)
        if cursor >= len(ordered):
            cursor = 0
        batch = ordered[cursor: cursor + batch_size]
        self._p1_cursors[exchange] = (cursor + batch_size) % max(len(ordered), 1)
        # [2026-08-06 修复] KlineFreshness 观察币每批强制置前（去重）：
        # 无论 cursor 轮转到 catalog 何处，观察币（含冷所 BTC/ETH 等）每批必刷，
        # 巡检 critical 不再因轮转位置长期滞留。
        _watch_in = [s for s in self._freshness_watch_symbols() if s in ordered]
        if _watch_in:
            batch = list(dict.fromkeys(_watch_in + batch))

        groups = self._p1_period_groups()
        gidx = int(self._p1_period_group_index.get(exchange) or 0) % max(len(groups), 1)
        periods = groups[gidx]
        self._p1_period_group_index[exchange] = (gidx + 1) % max(len(groups), 1)

        timeout_s = max(30, int(os.getenv("KLINE_P1_TIMEOUT_S", "90")))
        base_conc = int(os.getenv("KLINE_P1_CONCURRENCY", "4"))
        # HL 共享限速更严，默认降并发，避免整批 90s 超时归零
        if exchange == "hyperliquid":
            base_conc = min(base_conc, max(2, int(os.getenv("KLINE_P1_HL_CONCURRENCY", "3"))))
        conc = max(2, base_conc // 2) if self._p0_busy else base_conc
        sem = asyncio.Semaphore(conc)

        async def _limited(symbol, period):
            async with sem:
                return await self._p1_sync_symbol_period(symbol, period, exchange)

        tasks = [
            asyncio.create_task(_limited(sym, period))
            for sym in batch
            for period in periods
        ]
        ok, err = 0, 0
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout_s)
            for t in done:
                try:
                    if t.result() is True:
                        ok += 1
                    else:
                        err += 1
                except ExchangeRateLimitError as e:
                    # [2026-08-11 修复] 冷所限流只由冷所自己的限速器管理，
                    # 不要误触发 Asterdex 全局冷却标志（_rate_limited_ts）。
                    if exchange == "asterdex":
                        self._rate_limited_ts = time.time()
                        logger.warning(
                            f"[P1] Asterdex 限流/封禁，进入 %.0fs 冷却: %s",
                            self._rate_backoff_sec, e,
                        )
                    else:
                        logger.warning(f"[P1] {exchange} 限流/封禁（冷所独立冷却）: %s", e)
                    err += 1
                except Exception:
                    err += 1
            if pending:
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                err += len(pending)
                logger.warning(
                    f"[P1] batch timed out after {timeout_s}s "
                    f"(batch={len(batch)}, exchange={exchange}, periods={periods}, "
                    f"partial_ok={ok}, cancelled={len(pending)})"
                )

        logger.info(
            f"[P1] {exchange} FULL-period batch ok={ok} err={err} "
            f"size={len(batch)} cursor={self._p1_cursors.get(exchange)}/{len(ordered)} "
            f"periods={periods} group={gidx + 1}/{len(groups)} "
            f"conc={conc} sample={batch[:6]}"
        )
        try:
            from backend.services.kline_sync_meta import record_heartbeat
            record_heartbeat(
                exchange,
                pool="p1",
                period="*",
                symbols_ok=ok,
                symbols_fail=err,
                meta={
                    "batch": len(batch),
                    "catalog": len(ordered),
                    "cursor": self._p1_cursors.get(exchange),
                    "periods": periods,
                    "period_group": gidx,
                    "all_periods": self._p1_all_periods(),
                    "active_boost": exchange == active,
                    "mode": "full_warehouse",
                },
            )
        except Exception:
            pass

    async def _collect_symbol_kline(self, symbol: str, period: str = "1m", exchange_id: str = None) -> bool:
        """采集单个交易对指定周期的K线数据，采集成功后广播 + 失效缓存"""
        exchange_id = exchange_id or get_active_exchange()
        try:
            collector = ExchangeDataSourceFactory.get_collector(exchange_id)
            kline_data = await collector.fetch_current_kline(symbol, period)
            if not kline_data:
                return False

            success = await kline_service._insert_kline_data([kline_data])
            if not success:
                return False

            # 级联失效缓存：短周期更新 → 清理当前交易所相关长周期缓存
            kline_cache.invalidate_cascade(symbol, period, exchange=exchange_id)

            # WebSocket 广播：仅主交易所（前端 WS 仍绑定 active exchange）
            bar = {
                "open": kline_data.open_price,
                "high": kline_data.high_price,
                "low": kline_data.low_price,
                "close": kline_data.close_price,
                "volume": kline_data.volume,
                "timestamp": kline_data.timestamp,
            }
            try:
                from backend.services.live_kline_engine import live_kline_engine
                live_kline_engine.seed_bar(exchange_id, symbol, period, bar)
            except Exception:
                pass
            if exchange_id == get_active_exchange():
                broadcast_after_collection(symbol, period, bar)

            return True
        except ExchangeRateLimitError as e:
            # [2026-08-11 修复] 冷所限流走冷所独立冷却，不误触发 Asterdex 全局冷却。
            if exchange_id == "asterdex":
                self._rate_limited_ts = time.time()
                logger.warning(
                    f"限流/封禁 {symbol}/{period}@{exchange_id}，进入 %.0fs 冷却: %s",
                    self._rate_backoff_sec, e,
                )
            else:
                logger.debug(f"[P1] {exchange_id} 限流冷却中（冷所独立管理）: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to collect kline for {symbol}/{period}@{exchange_id}: {e}")
            return False

    async def _initial_backfill(self):
        """P2：启动时历史回补 — 等 P0 就绪后才跑，且按 active_exchange 过滤。

        规则引擎需要至少 MIN_1H_CANDLES_REQUIRED 根 1h K 线；
        不够的则从交易所拉取历史数据。禁止与 P0 抢同一分钟硬超时。
        """
        await asyncio.sleep(5)
        # 最多等 3 分钟让 P0 先跑完至少一轮
        for _ in range(36):
            if not self.running:
                return
            if self._p0_ready.is_set():
                break
            await asyncio.sleep(5)

        symbols = self._build_p0_symbols() or (kline_service.get_supported_symbols() or self.default_symbols)
        # [2026-08-04 修复] P2 全 catalog 扩展：KLINE_DEPTH_BACKFILL_SYMBOLS=all 时
        # 把 asterdex 全 catalog（含全部山寨币）并入检查，解决「山寨币 1h 仅 ~13 天、
        # 主流币周期缺失」的数据不全问题。山寨币排在热币之后，慢速渐进回填。
        p2_mode = os.getenv("KLINE_DEPTH_BACKFILL_SYMBOLS", "").strip().lower()
        if p2_mode in ("all", "catalog", "full"):
            try:
                from backend.services.kline_sync_meta import refresh_catalog_from_scanner
                catalog = refresh_catalog_from_scanner("asterdex") or []
                hot = {s.upper() for s in symbols}
                symbols = list(symbols) + [s.upper() for s in catalog if s.upper() not in hot]
                logger.info(
                    "[P2-Backfill] 全 catalog 模式: 热币 %d + 山寨 %d = %d",
                    len(hot), len(symbols) - len(hot), len(symbols),
                )
            except Exception as e:
                logger.warning(f"[P2-Backfill] catalog 扩展失败，退回热币: {e}")
        try:
            active_ex = (get_active_exchange() or "asterdex").strip().lower()
        except Exception:
            active_ex = "asterdex"
        if active_ex == "aster":
            active_ex = "asterdex"

        logger.info(
            f"[P2-Backfill] 开始检查 {len(symbols)} 个币历史完整性 @ {active_ex}"
        )

        from sqlalchemy import text as sa_text

        from backend.database.connection import MarketSessionLocal

        backfilled = []
        ok_n, fail_n = 0, 0

        for symbol in symbols:
            if not self.running:
                break
            # [2026-08-04 修复] 429 冷却：命中限流后 P2 暂停等待窗口回收，
            # 避免回填的连续请求继续顶满 Asterdex 滑动窗口。
            while time.time() - self._rate_limited_ts < self._rate_backoff_sec and self.running:
                await asyncio.sleep(5)
            if not self.running:
                break
            try:
                with MarketSessionLocal() as db:
                    row = db.execute(sa_text("""
                        SELECT COUNT(*) FROM crypto_klines
                        WHERE symbol = :sym AND period = '1h' AND exchange = :ex
                    """), {"sym": symbol.upper(), "ex": active_ex}).scalar()

                existing_count = row or 0

                need_backfill = False
                reason = ""

                if existing_count < MIN_1H_CANDLES_REQUIRED:
                    need_backfill = True
                    reason = (
                        f"[P2-Backfill] {symbol}@{active_ex}: 1h K线仅{existing_count}根"
                        f"（需 {MIN_1H_CANDLES_REQUIRED}），开始回补"
                    )
                else:
                    with MarketSessionLocal() as db:
                        cov = db.execute(sa_text("""
                            SELECT
                                COUNT(*)::float / NULLIF(
                                    (MAX("timestamp") - MIN("timestamp"))::float / 60 + 1,
                                    0
                                ) * 100 AS coverage_pct
                            FROM crypto_klines
                            WHERE symbol = :sym AND period = '1m' AND exchange = :ex
                        """), {"sym": symbol.upper(), "ex": active_ex}).scalar()

                    coverage_pct = cov or 100.0
                    if 0 < coverage_pct < MIN_1M_COVERAGE_PCT:
                        need_backfill = True
                        reason = (
                            f"[P2-Backfill] {symbol}@{active_ex}: 1h充足但 1m 覆盖率仅 "
                            f"{coverage_pct:.1f}%（阈值 {MIN_1M_COVERAGE_PCT}%），回补"
                        )
                    else:
                        logger.debug(
                            f"[P2-Backfill] {symbol}@{active_ex}: 1h={existing_count}, "
                            f"1m coverage={coverage_pct:.1f}%，跳过"
                        )
                        ok_n += 1

                if not need_backfill:
                    continue

                logger.info(reason)

                end_time = datetime.now(timezone.utc)
                backfill_days = BACKFILL_DAYS
                if backfill_days <= 0:
                    base = normalize_symbol(symbol)
                    listing_map = {"BTC": "2019-01-01", "ETH": "2019-01-01",
                                   "SOL": "2020-08-01", "BNB": "2019-01-01"}
                    listing_str = listing_map.get(base, "2022-01-01")
                    listing_dt = datetime.fromisoformat(listing_str).replace(tzinfo=timezone.utc)
                    backfill_days = max(30, (end_time - listing_dt).days)
                start_time = end_time - timedelta(days=backfill_days)

                total_collected = 0
                for period in self._short_backfill_periods:
                    if not self.running:
                        break
                    try:
                        # [2026-08-04 修复] 短周期全深度回填是 Asterdex WAF 429 主因：
                        # 1m 回填 400 天 = 576 批请求/币，与 P0(333/min)+market_flow 叠加
                        # 会突破交易所 2400 req/min 上限（实测 16:14 P0 整轮 0ok/333err）。
                        # 1m/3m/5m 由 P0 每分钟实时积累，只回填近 30 天足够近端覆盖；
                        # 15m/30m 规则引擎用近端数据足够，只回填近 90 天；
                        # 1h+ 才回填 BACKFILL_DAYS 全深度（规则引擎需要 1h ≥2190 根）。
                        _p_start = start_time
                        if period in ("1m", "3m", "5m"):
                            _p_start = max(start_time, end_time - timedelta(days=30))
                        elif period in ("15m", "30m"):
                            _p_start = max(start_time, end_time - timedelta(days=90))
                        collected = await kline_service.collect_historical_klines(
                            symbol, _p_start, end_time, period
                        )
                        total_collected += collected
                        if collected > 0:
                            logger.info(f"[P2-Backfill] {symbol}/{period}: 回补 {collected} 根")
                    except ExchangeRateLimitError as e:
                        # [2026-08-04 修复] P2 回填命中限流：进入全链冷却，
                        # 外层 while 会等待 backoff 结束后再继续。
                        self._rate_limited_ts = time.time()
                        logger.warning(
                            f"[P2-Backfill] {symbol} 限流/封禁，进入 %.0fs 冷却: %s",
                            self._rate_backoff_sec, e,
                        )
                        await asyncio.sleep(5)
                    except Exception as e:
                        logger.warning(f"[P2-Backfill] {symbol}/{period} 回补失败: {e}")
                    await asyncio.sleep(1.0)  # 比旧版更慢，避免抢 P0/P1

                if self.running:
                    try:
                        w_start = end_time - timedelta(days=400)
                        w_collected = await kline_service.collect_historical_klines(
                            symbol, w_start, end_time, "1w"
                        )
                        total_collected += w_collected
                        if w_collected > 0:
                            logger.info(f"[P2-Backfill] {symbol}/1w: 回补 {w_collected} 根")
                    except Exception as e:
                        logger.warning(f"[P2-Backfill] {symbol}/1w 回补失败: {e}")

                if total_collected > 0:
                    backfilled.append(symbol)
                    ok_n += 1
                    logger.info(f"[P2-Backfill] {symbol}: 共回补 {total_collected} 根 K 线")
                else:
                    fail_n += 1

                # [2026-08-04 修复] symbol 间间隔 2s→5s：P2 回填期间持续请求，
                # 与 P0 叠加会突破 Asterdex 2400 req/min；放慢节奏让出配额。
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                logger.info("[P2-Backfill] 回补任务被取消")
                return
            except Exception as e:
                fail_n += 1
                logger.warning(f"[P2-Backfill] {symbol} 回补异常: {e}")

        if backfilled:
            logger.info(f"[P2-Backfill] 历史数据回补完成: {', '.join(backfilled)}")
        else:
            logger.info("[P2-Backfill] 热币历史数据均已充足，无需回补")
        try:
            from backend.services.kline_sync_meta import record_heartbeat
            record_heartbeat(
                active_ex,
                pool="p2",
                period="history",
                symbols_ok=ok_n,
                symbols_fail=fail_n,
                meta={"backfilled": backfilled[:20]},
            )
        except Exception:
            pass

    async def _gap_detection_loop(self):
        """缺失检测循环 - 每小时执行一次"""
        logger.info("Starting gap detection loop")

        while self.running:
            try:
                # 等待1小时
                await asyncio.sleep(3600)

                if not self.running:
                    break

                # 执行缺失检测
                await self._detect_and_fill_gaps()

            except asyncio.CancelledError:
                logger.info("Gap detection loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in gap detection loop: {e}")

    async def _detect_and_fill_gaps(self):
        """检测并自动填补数据缺失"""
        logger.info("Starting gap detection and auto-fill")

        # 检查过去24小时的数据完整性
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=24)

        symbols = kline_service.get_supported_symbols()
        if not symbols:
            symbols = self.default_symbols

        for symbol in symbols:
            try:
                # 检测缺失的时间段
                missing_ranges = await kline_service.detect_missing_ranges(
                    symbol, start_time, end_time, "1m"
                )

                if missing_ranges:
                    logger.info(f"Found {len(missing_ranges)} missing ranges for {symbol}")

                    # 自动补充缺失数据（大 gap 拆分为 6h 块渐进补全）
                    for range_start, range_end in missing_ranges:
                        gap_hours = (range_end - range_start).total_seconds() / 3600
                        # [fix] P1-4: 大 gap 不再跳过，而是拆分为 6h 块逐段补全
                        if gap_hours > 6:
                            logger.info(f"Large gap ({gap_hours:.1f}h) for {symbol}, splitting into 6h chunks for backfill")
                            chunk_start = range_start
                            while chunk_start < range_end:
                                chunk_end = min(chunk_start + timedelta(hours=6), range_end)
                                try:
                                    collected = await kline_service.collect_historical_klines(
                                        symbol, chunk_start, chunk_end, "1m"
                                    )
                                    if collected > 0:
                                        logger.debug(f"Backfilled {collected} records for {symbol} [{chunk_start} → {chunk_end}]")
                                except Exception as chunk_err:
                                    logger.warning(f"Chunk backfill failed for {symbol} [{chunk_start} → {chunk_end}]: {chunk_err}")
                                chunk_start = chunk_end
                                await asyncio.sleep(1.5)  # 块间额外延时防限流
                            continue

                        collected = await kline_service.collect_historical_klines(
                            symbol, range_start, range_end, "1m"
                        )

                        if collected > 0:
                            logger.info(f"Auto-filled {collected} records for {symbol} from {range_start} to {range_end}")
                        else:
                            logger.warning(f"Failed to auto-fill gap for {symbol} from {range_start} to {range_end}")

                        # 避免API限流，每次补充后等待一下
                        await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error detecting gaps for {symbol}: {e}")

        logger.info("Gap detection and auto-fill completed")


# 全局实时采集器实例
realtime_collector = KlineRealtimeCollector()
