"""
binance_ticker_poller — Binance 永续全市场 ticker 轮询（实时参考价源）。

背景：
  Asterdex 是低流动性 DEX，其 /ticker/price 的 lastPrice 明显跳动约 30~60s
  才来一次（做市商钉价）。纸交易盯市/展示需要「时时跳」的实时价，故引入
  Binance 永续（fapi）ticker 作为参考价源：

  - 每 1s 拉一次全市场 /fapi/v1/ticker/price（742 币 / 46KB，权重极低）
  - 仅存内存 _prices（不 fan-out 到 price_cache/Hub，避免污染 Asterdex 成交价）
  - 由数据中心进程 /ticker/binance/{base} 暴露给主 API 进程

  注意：Binance 直连被墙，必须走 .env 注入的行情代理（127.0.0.1:1080）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any, Dict, Optional, Tuple

from backend.services.symbol_normalizer import normalize_symbol

logger = logging.getLogger(__name__)

BINANCE_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/price"
DEFAULT_INTERVAL_SEC = float(os.getenv("BINANCE_TICKER_INTERVAL_S", "1"))


def _default_proxy() -> Optional[str]:
    for key in (
        "MARKET_DATA_HTTP_PROXY",
        "BINANCE_HTTP_PROXY",
        "BINANCE_HTTPS_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
    ):
        val = os.getenv(key, "").strip()
        if val:
            return val
    return None


def _make_opener() -> urllib.request.OpenerDirector:
    proxy = _default_proxy()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        if proxy
        else urllib.request.ProxyHandler({})
    )


class BinanceTickerPoller:
    """Binance 永续全市场 ticker 轮询（线程 + urllib，走本地代理）。"""

    def __init__(self, interval_seconds: float = DEFAULT_INTERVAL_SEC):
        self.interval_seconds = max(0.5, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # base symbol -> (price, ts)
        self._prices: Dict[str, Tuple[float, float]] = {}
        self._last_error_ts = 0.0
        self._polls = 0
        self._last_poll_at = 0.0

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="binance-ticker-poller",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[BinanceTicker] 启动：全市场 ticker 每 %.1fs 轮询", self.interval_seconds
        )
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def get_price(self, symbol: str) -> Optional[float]:
        entry = self.get_price_with_ts(symbol)
        return float(entry[0]) if entry else None

    def get_price_with_ts(self, symbol: str) -> Optional[Tuple[float, float]]:
        sym = (symbol or "").upper().split("-")[0].split("/")[0]
        with self._lock:
            entry = self._prices.get(sym)
            return entry if entry else None

    def get_all_prices(self) -> Dict[str, float]:
        with self._lock:
            return {s: p for s, (p, _) in self._prices.items()}

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            count = len(self._prices)
        return {
            "running": self.is_running,
            "interval_sec": self.interval_seconds,
            "polls": self._polls,
            "last_poll_at": self._last_poll_at,
            "symbols": count,
        }

    def _fetch_once(self, opener: urllib.request.OpenerDirector) -> None:
        with opener.open(BINANCE_TICKER_URL, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        now_ts = time.time()
        if not isinstance(raw, list):
            return

        fresh: Dict[str, Tuple[float, float]] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            raw_symbol = str(item.get("symbol") or "")
            # 优先 USDT 本位（与 Asterdex 报价一致）；跳过 USDC 本位避免 base 冲突
            if raw_symbol.endswith("USDC"):
                continue
            if not raw_symbol.endswith("USDT"):
                continue
            base = normalize_symbol(raw_symbol)
            if not base:
                continue
            try:
                price = float(item.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            fresh[base] = (price, now_ts)

        if not fresh:
            return

        with self._lock:
            self._prices = fresh
            self._polls += 1
            self._last_poll_at = now_ts

    def _run(self) -> None:
        opener = _make_opener()
        while not self._stop.is_set():
            t0 = time.time()
            ok = True
            try:
                self._fetch_once(opener)
            except Exception as exc:
                ok = False
                now = time.time()
                if now - self._last_error_ts > 10:
                    logger.debug("[BinanceTicker] 轮询失败: %s", exc)
                    self._last_error_ts = now
            try:
                from backend.services.data_quality_monitor import get_data_quality_monitor
                get_data_quality_monitor().record_source_call(
                    "ticker_binance", success=ok,
                    latency_ms=round((time.time() - t0) * 1000, 1),
                    error="" if ok else f"poll failed ({exc.__class__.__name__})",
                )
            except Exception:
                pass
            elapsed = time.time() - t0
            wait = max(0.0, self.interval_seconds - elapsed)
            if wait > 0:
                self._stop.wait(wait)


binance_ticker_poller = BinanceTickerPoller()
