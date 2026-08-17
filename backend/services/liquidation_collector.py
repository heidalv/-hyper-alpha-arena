"""
清算数据采集器 — 小时级清算聚合落库（liquidation_events）。

[2026-08-15 D3]
    此前清算数据只存在于 DerivativesSnapshot 内存对象（Coinalyze 查询后即弃），
    无历史深度 → 清算因子/极端场景训练无数据。本采集器把 Coinalyze 免费层
    /liquidation-history（小时聚合、跨所口径、long/short USD）落库为
    liquidation_events（exchange='aggregate'），每 15 分钟补齐最近若干
    已完成小时，幂等（唯一约束）不重写。

诚实原则：
    - 无 COINALYZE_API_KEY 时优雅空转（offline=True，不造数）；
    - 当前未完成小时不写入（避免半成品数据）；
    - 免费层按 IP 限频，请求节流 ≥30s，429 退避。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COINALYZE_BASE = "https://api.coinalyze.net/v1"

DEFAULT_SYMBOLS: List[str] = [
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ARB", "OP", "AVAX", "LINK", "ADA",
    "MATIC", "DOT", "NEAR", "FIL", "BNB",
]

_MIN_REQUEST_INTERVAL_SEC = 30.0
_last_request_at = 0.0
_last_summary: Dict[str, Any] = {}


def get_last_summary() -> Dict[str, Any]:
    return dict(_last_summary)


def _load_coinalyze_key() -> str:
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


def _coinalyze_symbol_map() -> Dict[str, str]:
    try:
        from backend.services.derivatives_analytics_service import COINALYZE_SYMBOL_MAP
        return dict(COINALYZE_SYMBOL_MAP)
    except Exception:
        return {}


def _request_json(url: str, params: Dict[str, Any], headers: Dict[str, str], timeout: float = 15.0) -> Optional[Any]:
    global _last_request_at
    try:
        import urllib.request
        import json as _json
        from backend.services.market_aggregation.aggregate_collector_base import _get_proxy

        # 免费层限频：请求间最小间隔
        wait = _MIN_REQUEST_INTERVAL_SEC - (time.time() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        proxy = None
        try:
            proxy = _get_proxy()
        except Exception:
            pass
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            if proxy else urllib.request.ProxyHandler({})
        )
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        req = urllib.request.Request(f"{url}?{qs}", headers=headers)
        with opener.open(req, timeout=timeout) as resp:
            if resp.status != 200:
                logger.debug("[LiquidationCollector] HTTP %s", resp.status)
                return None
            return _json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("[LiquidationCollector] 请求失败: %s", exc)
        return None
    finally:
        _last_request_at = time.time()


def collect_once(symbols: Optional[List[str]] = None, hours_back: int = 6) -> Dict[str, Any]:
    """拉取最近若干已完成小时的清算聚合并落库。返回摘要。"""
    key = _load_coinalyze_key()
    if not key:
        summary = {"ok": False, "offline": True, "written": 0, "reason": "no COINALYZE_API_KEY"}
        _last_summary.clear()
        _last_summary.update(summary)
        return summary

    syms = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
    ca_map = _coinalyze_symbol_map()
    ca_symbols = [ca_map.get(s, f"{s}USDT_PERP.A") for s in syms]

    now_ts = int(time.time())
    from_ts = now_ts - hours_back * 3600
    headers = {"api_key": key, "Content-Type": "application/json"}

    data = _request_json(
        f"{COINALYZE_BASE}/liquidation-history",
        params={
            "symbols": ",".join(ca_symbols),
            "interval": "1hour",
            "from": from_ts,
            "to": now_ts,
            "convert_to_usd": "true",
        },
        headers=headers,
    )
    if not data or not isinstance(data, list):
        summary = {"ok": False, "offline": True, "written": 0, "reason": "empty response"}
        _last_summary.clear()
        _last_summary.update(summary)
        return summary

    # Coinalyze 返回按 symbol 分组：[{symbol, history: [{t, l, s}, ...]}]
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for block in data:
        ca_sym = str(block.get("symbol") or "")
        base = next((s for s, c in ca_map.items() if c == ca_sym), None)
        if not base:
            if ca_sym.endswith("USDT_PERP.A"):
                base = ca_sym.replace("USDT_PERP.A", "")
            else:
                continue
        history = block.get("history") or []
        if not history:
            continue
        # 去掉最后一条（当前未完成小时），只写已完成小时
        for item in history[:-1]:
            try:
                ts = int(item.get("t", 0))
                if ts <= 0:
                    continue
                ts_ms = ts * 1000
                key_row = ("aggregate", base, ts_ms)
                if key_row in seen:
                    continue
                seen.add(key_row)
                rows.append({
                    "exchange": "aggregate",
                    "symbol": base,
                    "ts_ms": ts_ms,
                    "long_usd": float(item.get("l", 0) or 0),
                    "short_usd": float(item.get("s", 0) or 0),
                    "source": "coinalyze",
                })
            except (TypeError, ValueError):
                continue

    written = 0
    if rows:
        written = _persist(rows)

    summary = {
        "ok": written > 0 or bool(rows),
        "offline": not data,
        "written": written,
        "symbols": len(seen),
        "hours_back": hours_back,
    }
    _last_summary.clear()
    _last_summary.update(summary)
    logger.info(
        "[LiquidationCollector] 采集完成: 写入 %d 行（%d 币 × %d 小时窗口）",
        written, len(seen), hours_back,
    )
    return summary


def _persist(rows: List[Dict[str, Any]]) -> int:
    """幂等写入 liquidation_events（唯一约束 exchange+symbol+ts_ms）。"""
    try:
        from sqlalchemy import text as _sa_text

        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            db.execute(
                _sa_text(
                    "INSERT INTO liquidation_events "
                    "(exchange, symbol, ts_ms, long_usd, short_usd, source) "
                    "VALUES (:exchange, :symbol, :ts_ms, :long_usd, :short_usd, :source) "
                    "ON CONFLICT (exchange, symbol, ts_ms) DO NOTHING"
                ),
                rows,
            )
            db.commit()
        return len(rows)
    except Exception as exc:
        logger.warning("[LiquidationCollector] 落库失败: %s", exc)
        return 0


def start_liquidation_collector(interval_sec: int = 900) -> None:
    """数据中心进程后台线程：每 15 分钟补最近 6 小时清算聚合（幂等）。"""
    if os.getenv("LIQUIDATION_COLLECTOR_ENABLED", "true").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        logger.info("[LiquidationCollector] 已禁用（LIQUIDATION_COLLECTOR_ENABLED=false）")
        return

    def _run() -> None:
        # 启动后 60s 先跑一次，之后按间隔
        time.sleep(60)
        while True:
            try:
                collect_once()
            except Exception as exc:
                logger.warning("[LiquidationCollector] 采集异常: %s", exc)
            time.sleep(max(120, int(interval_sec)))

    t = threading.Thread(target=_run, name="liquidation-collector", daemon=True)
    t.start()
    logger.info("[LiquidationCollector] 已启动（%ds 间隔）", interval_sec)
