"""
独立数据中心进程入口。

与 FastAPI 交易主服务解耦：主服务重启 / reload 不再中断 K 线与 ticker 采集。

启动方式（任选）：
  python -m backend.workers.market_data_center
  scripts\\start-data-center.bat

环境变量：
  DATA_CENTER_MODE=standalone   # 主服务见此值时跳过内嵌采集
  DATA_CENTER_HEALTH_PORT=9100  # 健康检查 HTTP 端口
  FEATURE / KLINE_*             # 与主服务共用 .env
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

# 保证仓库根在 path 上
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.chdir(_ROOT)

# 尽早加载 .env，再 import 会读开关的模块
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env", override=False)
except Exception:
    pass

# 独立进程模式标记（主服务据此跳过内嵌采集）
os.environ.setdefault("DATA_CENTER_MODE", "standalone")
os.environ["DATA_CENTER_PROCESS"] = "1"

logging.basicConfig(
    level=os.getenv("DATA_CENTER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_ROOT / "logs" / "data-center.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("data_center_worker")

_STATE: Dict[str, Any] = {
    "started_at": None,
    "ok": False,
    "components": {},
    "last_error": None,
    "_comp_health_cache": None,
}

# [2026-08-15 P2-1] 各组件可观测新鲜度阈值（超过即 stale）
_COMP_STALE_SEC = {
    "kline_realtime_collector": 300,   # P0 每分钟一轮，>5min 视为停摆
    "asterdex_ticker_poller": 30,      # 2s 通道，>30s 视为停摆
    "binance_ticker_poller": 30,       # 1s 通道，>30s 视为停摆
    "market_flow": 300,                # trades/orderbook 30s 轮询
    "multi_venue_funding": 900,        # 300s 轮询
    "depth_backfill": 6 * 3600,        # 每 6h 调度
    "freshness_inspector": 900,        # 每 5min 巡检
    "live_kline_engine": 120,          # 10s 刷新
}


def _db_max_ts(table: str, ts_col: str, where: str = "", is_ms: bool = False) -> Optional[float]:
    """Market DB 中某表最大时间戳（统一换算为 epoch 秒）。"""
    try:
        from sqlalchemy import text as _sa_text

        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            row = db.execute(
                _sa_text(f"SELECT MAX({ts_col}) FROM {table} {where}")
            ).scalar()
        if row is None:
            return None
        v = float(row)
        return (v / 1000.0) if is_ms else v
    except Exception as e:
        logger.debug("[DataCenter] _db_max_ts(%s) failed: %s", table, e)
        return None


def _compute_component_health() -> Dict[str, Any]:
    """从可观测证据计算各组件 last_success_ts/age/stale（30s 缓存）。

    [2026-08-15 P2-1] 原 /health 只有组件 up/fail 字符串，采集线程假活
    （线程在但数据不再更新）无法被发现。现增加基于真实数据的 staleness：
      - ticker：poller 内存价（BTC）
      - kline：crypto_klines asterdex BTC 1m 最新 bar
      - market_flow：market_trades_aggregated asterdex 最新窗口
      - funding：perp_funding 最新行
      其余组件仅报状态（无稳定可观测数据源）。
    """
    now = time.time()
    cached = _STATE.get("_comp_health_cache")
    if cached and (now - cached[0]) < 30:
        return cached[1]

    out: Dict[str, Any] = {}

    def add(name: str, ts: Optional[float], source: str) -> None:
        if ts and ts > 0:
            age = max(0.0, now - ts)
            out[name] = {
                "last_success_ts": round(ts, 1),
                "age_sec": round(age, 1),
                "stale": age > _COMP_STALE_SEC.get(name, 99999),
                "source": source,
            }
        else:
            out[name] = {"last_success_ts": None, "age_sec": None, "stale": True, "source": source}

    # ticker（内存价，含时间戳）
    try:
        from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
        entry = asterdex_ticker_poller.get_price_with_ts("BTC")
        add("asterdex_ticker_poller", float(entry[1]) if entry and entry[1] else None, "poller_memory")
    except Exception:
        add("asterdex_ticker_poller", None, "poller_error")

    # Binance 实时参考价（内存价，含时间戳）
    try:
        from backend.services.binance_ticker_poller import binance_ticker_poller
        entry = binance_ticker_poller.get_price_with_ts("BTC")
        add("binance_ticker_poller", float(entry[1]) if entry and entry[1] else None, "poller_memory")
    except Exception:
        add("binance_ticker_poller", None, "poller_error")

    # K 线（DB 最新 1m bar，秒）— 跟随 active_exchange（binance/asterdex）
    _active_ex = "asterdex"
    try:
        from backend.services.exchange_config import get_active_exchange
        _active_ex = (get_active_exchange() or "asterdex").strip().lower()
        if _active_ex == "aster":
            _active_ex = "asterdex"
    except Exception:
        pass
    add(
        "kline_realtime_collector",
        _db_max_ts(
            "crypto_klines", "timestamp",
            f"WHERE exchange='{_active_ex}' AND symbol='BTC' AND period='1m'",
            is_ms=False,
        ),
        "crypto_klines",
    )

    # 市场流（trades 聚合，毫秒）— 跟随 active_exchange
    add(
        "market_flow",
        _db_max_ts(
            "market_trades_aggregated", "timestamp",
            f"WHERE exchange='{_active_ex}'", is_ms=True,
        ),
        "market_trades_aggregated",
    )

    # 资金费率（毫秒）
    add("multi_venue_funding", _db_max_ts("perp_funding", "timestamp", "", is_ms=True), "perp_funding")

    _STATE["_comp_health_cache"] = (now, out)
    return out


def _health_payload() -> Dict[str, Any]:
    up = None
    if _STATE.get("started_at"):
        up = round(time.time() - float(_STATE["started_at"]), 1)
    comp_health = _compute_component_health()
    stale_names = [n for n, h in comp_health.items() if h.get("stale")]
    # ok 判定升级：K 线采集器 up 且 K 线数据新鲜；其余组件 stale 只降级标注，
    # 不整体 503（保持与主服务 data_health 的兼容性，避免误杀交易）。
    kline_ok = bool(_STATE.get("ok")) and not comp_health.get(
        "kline_realtime_collector", {}
    ).get("stale", True)
    return {
        "service": "market-data-center",
        "mode": "standalone",
        "ok": bool(kline_ok),
        "uptime_sec": up,
        "components": _STATE.get("components") or {},
        "component_health": comp_health,
        "stale_components": stale_names,
        "last_error": _STATE.get("last_error"),
    }


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        import json
        from urllib.parse import urlparse

        parsed = urlparse(self.path)
        path = parsed.path

        # 秒级 ticker 跨进程通道（backend 主服务经此取 2s 全市场最新价）
        # 注意：/ticker/all 与 /ticker/stats 必须先于前缀匹配，否则会被
        # "/ticker/" 前缀处理器吞掉（历史 bug：/ticker/all 返回 symbol=ALL null）。
        if path == "/ticker/all":
            self._handle_ticker_all()
            return
        if path == "/ticker/stats":
            self._handle_ticker_stats()
            return
        # [2026-08-18] Binance 全市场参考价批量出口（1s 通道，主 API 总览秒级叠加用）
        if path == "/ticker/binance/all":
            self._handle_ticker_binance_all()
            return
        if path.startswith("/ticker/binance/"):
            self._handle_ticker_binance(path[len("/ticker/binance/"):])
            return
        if path.startswith("/ticker/"):
            self._handle_ticker(path[len("/ticker/"):])
            return
        if path not in ("/", "/health", "/healthz", "/ready"):
            self.send_response(404)
            self.end_headers()
            return
        payload = _health_payload()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        # [2026-08-15 P2-1] HTTP 状态与 payload.ok 一致（K 线采集器 up 且数据新鲜）
        code = 200 if payload.get("ok") else 503
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_ticker(self, raw_symbol: str) -> None:
        """GET /ticker/{symbol} → 本进程 asterdex ticker poller 内存价（秒级）。"""
        import json
        from urllib.parse import unquote

        try:
            from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
            entry = asterdex_ticker_poller.get_price_with_ts(unquote(raw_symbol))
            if entry:
                payload = {
                    "symbol": unquote(raw_symbol).upper(),
                    "price": float(entry[0]),
                    "ts": float(entry[1]),
                }
            else:
                payload = {"symbol": unquote(raw_symbol).upper(), "price": None, "ts": None}
        except Exception as exc:  # poller 未就绪等
            payload = {"symbol": unquote(raw_symbol).upper(), "price": None, "ts": None, "error": str(exc)}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_ticker_binance(self, raw_symbol: str) -> None:
        """GET /ticker/binance/{symbol} → Binance 永续实时参考价（1s 通道）。"""
        import json
        from urllib.parse import unquote

        try:
            from backend.services.binance_ticker_poller import binance_ticker_poller
            entry = binance_ticker_poller.get_price_with_ts(unquote(raw_symbol))
            if entry:
                payload = {
                    "symbol": unquote(raw_symbol).upper(),
                    "price": float(entry[0]),
                    "ts": float(entry[1]),
                }
            else:
                payload = {"symbol": unquote(raw_symbol).upper(), "price": None, "ts": None}
        except Exception as exc:
            payload = {"symbol": unquote(raw_symbol).upper(), "price": None, "ts": None, "error": str(exc)}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_ticker_all(self) -> None:
        """GET /ticker/all → 全市场价格快照 {symbol: price}。"""
        import json

        try:
            from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
            prices = asterdex_ticker_poller.get_all_prices()
        except Exception as exc:
            prices = {}
            logger.debug("[DataCenter] /ticker/all error: %s", exc)
        body = json.dumps(
            {"count": len(prices), "ts": time.time(), "prices": prices},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_ticker_binance_all(self) -> None:
        """GET /ticker/binance/all → 全市场 Binance 实时参考价快照 {symbol: price}。"""
        import json

        try:
            from backend.services.binance_ticker_poller import binance_ticker_poller
            prices = binance_ticker_poller.get_all_prices()
        except Exception as exc:
            prices = {}
            logger.debug("[DataCenter] /ticker/binance/all error: %s", exc)
        body = json.dumps(
            {"count": len(prices), "ts": time.time(), "prices": prices},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_ticker_stats(self) -> None:
        """GET /ticker/stats → 全市场 24h 统计（涨跌幅/24h 高低/成交量）。

        24h 统计只在数据中心进程的 poller 内存里（standalone 模式下主 API
        进程的 poller 不启动），主服务经此通道取官方 24hr ticker 数据。
        """
        import json

        try:
            from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
            stats = asterdex_ticker_poller.get_all_stats()
        except Exception as exc:
            stats = {}
            logger.debug("[DataCenter] /ticker/stats error: %s", exc)
        body = json.dumps(
            {"count": len(stats), "ts": time.time(), "stats": stats},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A003
        return


def _start_health_server(port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    t = threading.Thread(target=httpd.serve_forever, name="dc-health", daemon=True)
    t.start()
    logger.info("[DataCenter] health http://0.0.0.0:%s/health", port)
    return httpd


async def _run_collectors(stop: asyncio.Event) -> None:
    from backend.core.tenant import set_system_identity

    set_system_identity()

    # [2026-08-15 D4] 本进程是数据中心采集层本体：允许聚合采集器（鲸鱼/盘口/OI）
    # 直连交易所采集（aggregate_collector_base 的 DC_ONLY 守卫对本进程放行）。
    os.environ["_DC_WORKER_PROCESS"] = "1"

    comps: Dict[str, str] = {}
    _STATE["components"] = comps
    _STATE["started_at"] = time.time()

    # 1) K 线 P0/P1/P2
    try:
        from backend.services.kline_realtime_collector import realtime_collector

        await realtime_collector.start()
        comps["kline_realtime_collector"] = "up"
        logger.info("[DataCenter] kline_realtime_collector started")
    except Exception as e:
        comps["kline_realtime_collector"] = f"fail:{e}"
        _STATE["last_error"] = str(e)
        logger.exception("[DataCenter] kline collector failed")

    # 2) Asterdex 秒级 ticker
    try:
        from backend.services.asterdex_ticker_poller import asterdex_ticker_poller

        # [2026-08-15 D5] 秒级 ticker 落库仅在数据中心进程开启（主 API 进程
        # embedded 模式不落库，避免双写）。
        os.environ.setdefault("TICKER_SNAPSHOT_PERSIST", "true")
        asterdex_ticker_poller.start()
        comps["asterdex_ticker_poller"] = "up"
    except Exception as e:
        comps["asterdex_ticker_poller"] = f"fail:{e}"
        logger.warning("[DataCenter] ticker poller: %s", e)

    # 2b) Binance 实时参考价（1s 通道，仅供盯市/展示，不参与成交）
    try:
        from backend.services.binance_ticker_poller import binance_ticker_poller

        binance_ticker_poller.start()
        comps["binance_ticker_poller"] = "up"
        logger.info("[DataCenter] binance_ticker_poller started")
    except Exception as e:
        comps["binance_ticker_poller"] = f"fail:{e}"
        logger.warning("[DataCenter] binance ticker poller: %s", e)

    # 3) 当前 forming K 线
    try:
        from backend.services.live_kline_engine import live_kline_engine

        live_kline_engine.start()
        comps["live_kline_engine"] = "up"
    except Exception as e:
        comps["live_kline_engine"] = f"fail:{e}"
        logger.warning("[DataCenter] live_kline_engine: %s", e)

    # 4) 深度回填（受 env 开关）
    try:
        from backend.services.kline_history_sync import depth_backfill_runner

        depth_backfill_runner.start()
        comps["depth_backfill"] = "up"
    except Exception as e:
        comps["depth_backfill"] = f"fail:{e}"
        logger.info("[DataCenter] depth_backfill: %s", e)

    # 5) 新鲜度巡检
    try:
        from backend.services.kline_freshness_inspector import kline_freshness_inspector

        kline_freshness_inspector.start()
        comps["freshness_inspector"] = "up"
    except Exception as e:
        comps["freshness_inspector"] = f"fail:{e}"
        logger.info("[DataCenter] freshness: %s", e)

    # 6) 市场流（盘口/成交/OI）— 默认 Asterdex（禁止默认 Hyperliquid）
    try:
        from backend.services.market_flow import market_flow_registry, register_defaults
        from backend.config import settings as _settings

        register_defaults()
        active_exchanges = list(
            getattr(_settings, "ACTIVE_MARKET_FLOW_EXCHANGES", None)
            or ["asterdex"]
        )
        if os.getenv("MARKET_FLOW_DISABLE_ASTERDEX", "").lower() in (
            "1", "true", "yes", "on",
        ):
            active_exchanges = [e for e in active_exchanges if e != "asterdex"]
        if not active_exchanges:
            active_exchanges = ["asterdex"]
        cvd_window = getattr(_settings, "CVD_AGGREGATION_WINDOW_SECONDS", 15)
        symbols_map = {}
        for ex in active_exchanges:
            if ex in ("asterdex", "binance"):
                symbols_map[ex] = ["BTC", "ETH", "SOL", "ADA", "BNB", "XRP", "DOGE", "AVAX", "LINK", "UNI"]
            else:
                symbols_map[ex] = None
        results = market_flow_registry.start_all(
            symbols_map=symbols_map,
            exchanges=active_exchanges,
            aggregation_window_seconds=cvd_window,
        )
        comps["market_flow"] = f"up:{results}"
        logger.info("[DataCenter] market_flow %s", results)
    except Exception as e:
        comps["market_flow"] = f"skip:{e}"
        logger.info("[DataCenter] market_flow: %s", e)

    # 7) 多所资金费率常驻（独立 DC 默认打开）
    try:
        os.environ.setdefault("MULTI_VENUE_FUNDING_COLLECTOR_ENABLED", "true")
        from backend.services.scheduler import start_multi_venue_funding_collector

        start_multi_venue_funding_collector()
        comps["multi_venue_funding"] = "up"
        logger.info("[DataCenter] multi_venue_funding started")
    except Exception as e:
        comps["multi_venue_funding"] = f"skip:{e}"
        logger.info("[DataCenter] multi_venue_funding: %s", e)

    # 8) [2026-08-15 D1] 资金费率历史回填（后台线程，每周重跑补缺口）
    if os.getenv("MULTI_VENUE_FUNDING_BACKFILL_ENABLED", "true").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        try:
            from backend.services.multi_venue_funding_collector import start_funding_backfill_thread

            days = 90
            try:
                days = max(7, min(365, int(os.getenv("MULTI_VENUE_FUNDING_BACKFILL_DAYS", "90"))))
            except (TypeError, ValueError):
                pass
            start_funding_backfill_thread(days=days)
            comps["funding_backfill"] = "up"
            logger.info("[DataCenter] funding history backfill started (days=%d)", days)
        except Exception as e:
            comps["funding_backfill"] = f"skip:{e}"
            logger.info("[DataCenter] funding backfill: %s", e)

    # 9) [2026-08-15 D3] 清算小时聚合落库（Coinalyze 免费层，每 15 分钟）
    try:
        from backend.services.liquidation_collector import start_liquidation_collector

        start_liquidation_collector(interval_sec=900)
        comps["liquidation_collector"] = "up"
    except Exception as e:
        comps["liquidation_collector"] = f"skip:{e}"
        logger.info("[DataCenter] liquidation_collector: %s", e)

    # 10) [2026-08-15 D4] 多所鲸鱼/大单聚合采集（binance/bybit/okx 逐笔成交 >$50K，
    # 每 45s 一轮，落 whale_activities；主进程消费方从仓库读回）
    try:
        import threading as _threading

        from backend.services.market_aggregation.aggregate_whale_collector import (
            WHALE_THRESHOLD_USD,
            aggregate_whale_collector,
        )
        from backend.services.kline_realtime_collector import get_research_priority_symbols

        def _whale_loop() -> None:
            interval = 45
            try:
                interval = max(20, min(300, int(os.getenv("AGG_WHALE_INTERVAL_S", "45"))))
            except (TypeError, ValueError):
                pass
            while True:
                try:
                    syms = list(get_research_priority_symbols(limit=30)) or []
                    for _s in ("BTC", "ETH", "SOL", "BNB"):
                        if _s not in syms:
                            syms.append(_s)
                    aggregate_whale_collector.collect(syms)
                except Exception as exc:
                    logger.warning("[DataCenter] whale collect: %s", exc)
                time.sleep(interval)

        _t = _threading.Thread(target=_whale_loop, name="aggregate-whale", daemon=True)
        _t.start()
        comps["aggregate_whale"] = "up"
        logger.info("[DataCenter] aggregate_whale started (threshold=USD %s)", WHALE_THRESHOLD_USD)
    except Exception as e:
        comps["aggregate_whale"] = f"skip:{e}"
        logger.info("[DataCenter] aggregate_whale: %s", e)

    # 至少 K 线起来才算 ok
    _STATE["ok"] = comps.get("kline_realtime_collector") == "up"
    logger.info("[DataCenter] ready ok=%s components=%s", _STATE["ok"], comps)

    await stop.wait()

    # 优雅停机
    try:
        from backend.services.market_flow import market_flow_registry

        market_flow_registry.stop_all()
    except Exception:
        pass
    try:
        from backend.services.kline_realtime_collector import realtime_collector

        await realtime_collector.stop()
    except Exception:
        pass
    logger.info("[DataCenter] stopped")


def main() -> int:
    (_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    port = int(os.getenv("DATA_CENTER_HEALTH_PORT", "9100"))
    httpd = _start_health_server(port)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop = asyncio.Event()

    def _ask_stop(*_args):
        logger.info("[DataCenter] signal received, shutting down…")
        try:
            loop.call_soon_threadsafe(stop.set)
        except Exception:
            stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _ask_stop)
        except Exception:
            pass

    try:
        loop.run_until_complete(_run_collectors(stop))
    except KeyboardInterrupt:
        logger.info("[DataCenter] KeyboardInterrupt")
        stop.set()
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
