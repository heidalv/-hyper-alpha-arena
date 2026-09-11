# -*- coding: utf-8 -*-
"""回测用资金费率历史加载（perp_funding 真实落库数据）。

[2026-09 修复 P1-6] 此前回测资金费成本用常数 0.0001/8h 模拟，90 天历史回填管道
采集的多所真实费率从不进入回测成本，导致资金费敏感型策略无法被历史验证。
本模块提供：
  - load_funding_series(symbol, exchange)  → (ts_ms 升序, rate 原始8h) 或 None
  - funding_rate_at(symbol, ts_s)          → 某时刻生效费率（asof）
  - mean_funding_in_window(symbol, start_s, end_s) → 持仓窗口均值（|rate|）

场所优先级：指定所 → binance → bybit → okx → 任意所。
数据缺失/无网络时回退默认常数（0.0001），绝不伪造。
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_FUNDING_RATE = 0.0001  # 0.01%/8h（与 BacktestConfig.avg_funding_rate 同源）

_VENUE_PRIORITY = ("binance", "bybit", "okx", "gateio", "hyperliquid", "asterdex")

_cache: Dict[Tuple[str, Optional[str]], Optional[Tuple[np.ndarray, np.ndarray]]] = {}
_cache_lock = threading.Lock()
_MAX_CACHE_ENTRIES = 96


def _base_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    s = s.split(":")[0].split("/")[0].split("-")[0].replace("USDT", "").strip()
    return s or (symbol or "").strip().upper()


def load_funding_series(
    symbol: str,
    exchange: Optional[str] = None,
    max_age_days: int = 400,
) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """读取 perp_funding 历史：(ts_ms 升序 ndarray, funding_rate ndarray)。

    无数据返回 None。结果缓存于进程内存（同一 (symbol, exchange) 只查一次）。
    """
    base = _base_symbol(symbol)
    ex = (exchange or "").strip().lower() or None
    key = (base, ex)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    result: Optional[Tuple[np.ndarray, np.ndarray]] = None
    try:
        import time

        from sqlalchemy import text as _sa_text

        from backend.database.connection import MarketSessionLocal

        cutoff_ms = int((time.time() - max_age_days * 86400) * 1000)
        ex_clause = "AND exchange = :ex" if ex else ""
        db = MarketSessionLocal()
        try:
            rows = db.execute(
                _sa_text(
                    f"SELECT timestamp, funding_rate, exchange FROM perp_funding "
                    f"WHERE symbol = :sym AND timestamp >= :cutoff {ex_clause} "
                    f"ORDER BY timestamp"
                ),
                {"sym": base, "cutoff": cutoff_ms, "ex": ex or ""},
            ).mappings().all()
        finally:
            db.close()
        if rows:
            if ex:
                ts = np.array([r["timestamp"] for r in rows], dtype=np.int64)
                rates = np.array([float(r["funding_rate"] or 0) for r in rows], dtype=np.float64)
            else:
                # 无指定所：按场所优先级挑一个数据最多的所
                best_ex = None
                counts: Dict[str, int] = {}
                for r in rows:
                    counts[str(r["exchange"])] = counts.get(str(r["exchange"]), 0) + 1
                for v in _VENUE_PRIORITY:
                    if counts.get(v, 0) > 0:
                        best_ex = v
                        break
                if best_ex is None and counts:
                    best_ex = max(counts, key=lambda k: counts[k])
                _rows = [r for r in rows if str(r["exchange"]) == best_ex]
                ts = np.array([r["timestamp"] for r in _rows], dtype=np.int64)
                rates = np.array([float(r["funding_rate"] or 0) for r in _rows], dtype=np.float64)
            # 去重（同 timestamp 取后值）
            if len(ts) > 1:
                uniq, idx = np.unique(ts, return_index=True)
                ts, rates = ts[np.sort(idx)], rates[np.sort(idx)]
            if len(ts) > 0:
                result = (ts, rates)
    except Exception as exc:
        logger.debug("[FundingHistory] 加载失败 %s/%s: %s", base, ex, exc)

    with _cache_lock:
        if len(_cache) >= _MAX_CACHE_ENTRIES:
            _cache.clear()
        _cache[key] = result
    return result


def funding_rate_at(
    symbol: str,
    ts_s: float,
    exchange: Optional[str] = None,
    default: float = DEFAULT_FUNDING_RATE,
) -> float:
    """某时刻生效的资金费率（最近一条 ≤ ts 的记录，asof）。无历史 → default。"""
    series = load_funding_series(symbol, exchange)
    if not series:
        return default
    ts, rates = series
    ts_ms = np.int64(round(ts_s * 1000))
    idx = int(np.searchsorted(ts, ts_ms, side="right")) - 1
    if idx < 0:
        return default
    return float(rates[idx])


def mean_funding_in_window(
    symbol: str,
    start_s: float,
    end_s: float,
    exchange: Optional[str] = None,
    default: float = DEFAULT_FUNDING_RATE,
) -> float:
    """持仓窗口内的平均 |资金费率|（asof 值参与，近似每 8h 结算成本口径）。

    无历史 → default。用于回测平仓时的持仓期资金费成本。
    """
    series = load_funding_series(symbol, exchange)
    if not series:
        return default
    ts, rates = series
    start_ms = np.int64(round(start_s * 1000))
    end_ms = np.int64(round(end_s * 1000))
    if end_ms <= start_ms:
        return default
    idx_start = int(np.searchsorted(ts, start_ms, side="right")) - 1
    mask = (ts >= start_ms) & (ts <= end_ms)
    vals = []
    if idx_start >= 0:
        vals.append(float(rates[idx_start]))
    vals.extend(float(v) for v in rates[mask])
    if not vals:
        return default
    return float(np.mean(np.abs(np.asarray(vals, dtype=np.float64))))
