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
}


def _health_payload() -> Dict[str, Any]:
    up = None
    if _STATE.get("started_at"):
        up = round(time.time() - float(_STATE["started_at"]), 1)
    return {
        "service": "market-data-center",
        "mode": "standalone",
        "ok": bool(_STATE.get("ok")),
        "uptime_sec": up,
        "components": _STATE.get("components") or {},
        "last_error": _STATE.get("last_error"),
    }


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        import json
        from urllib.parse import urlparse

        parsed = urlparse(self.path)
        path = parsed.path

        # 秒级 ticker 跨进程通道（backend 主服务经此取 2s 全市场最新价）
        if path.startswith("/ticker/"):
            self._handle_ticker(path[len("/ticker/"):])
            return
        if path == "/ticker/all":
            self._handle_ticker_all()
            return
        if path not in ("/", "/health", "/healthz", "/ready"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(_health_payload(), ensure_ascii=False).encode("utf-8")
        code = 200 if _STATE.get("ok") else 503
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

        asterdex_ticker_poller.start()
        comps["asterdex_ticker_poller"] = "up"
    except Exception as e:
        comps["asterdex_ticker_poller"] = f"fail:{e}"
        logger.warning("[DataCenter] ticker poller: %s", e)

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
            if ex == "asterdex":
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
