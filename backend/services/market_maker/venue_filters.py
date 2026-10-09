# -*- coding: utf-8 -*-
"""[h667 2026-10-01] 模拟做市按 Asterdex 真实交易条件成交(用户指令:
"模拟做市也要按照这个条件来做")。条件来源:官方公开 /fapi/v3/exchangeInfo
(scripts/fetch_asterdex_exchange_info.py 落盘 data/asterdex_exchange_info.json)。

强制项(与实盘 V3 完全同源):
  1. LOT_SIZE:数量按 stepSize 对齐且 ≥ minQty(UNI stepSize=1.0 ⇒ 必须整币!);
  2. PRICE_FILTER:价格按 tickSize 对齐;
  3. MIN_NOTIONAL:名义 ≥ $5(减仓腿豁免,与官方一致);
  4. PERCENT_PRICE:挂单价须在标记价 ±2%~±5% 带内(BNB ±2%,UNI/ENA ±5%);
  5. MAX_NUM_ORDERS:每币同时 ≤200 张(纸面最多 2 张,恒过);
  6. 单量级控制:每 tick 撤改折算订单操作数,按 1200 单/分/账户 计速(超限=模拟 429)。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
INFO_FILE = ROOT / "data" / "asterdex_exchange_info.json"
MAX_ORDERS_PER_MIN = 1200        # 官方:每账户订单速率上限
STALE_WARN_S = 24 * 3600

_cache: Optional[Dict[str, Any]] = None
_cache_ts = 0.0


def load_filters(force: bool = False) -> Dict[str, Dict[str, float]]:
    """每币过滤器字典(缓存 10 分钟)。文件缺失/过期只告警不报错(逐币回退=放行)。"""
    global _cache, _cache_ts
    now = time.time()
    if not force and _cache is not None and now - _cache_ts < 600:
        return _cache
    out: Dict[str, Dict[str, float]] = {}
    try:
        if not INFO_FILE.exists():
            logger.warning("[h667] exchangeInfo 文件缺失:先跑 "
                           "scripts/fetch_asterdex_exchange_info.py")
            _cache, _cache_ts = out, now
            return out
        data = json.loads(INFO_FILE.read_text(encoding="utf-8"))
        out = data.get("symbols") or {}
        fetched = float(data.get("fetched_at") or 0.0)
        if fetched and now - fetched / 1000.0 > STALE_WARN_S:
            logger.warning("[h667] exchangeInfo 已过期 %.1fh,建议重跑 fetch 脚本",
                           (now - fetched / 1000.0) / 3600.0)
    except Exception as e:
        logger.warning("[h667] exchangeInfo 读取失败(放行): %s", e)
    _cache, _cache_ts = out, now
    return out


def filters_for(symbol: str) -> Dict[str, float]:
    s = str(symbol or "").upper()
    if not s.endswith("USDT"):
        s = f"{s}USDT"
    return dict(load_filters().get(s) or {})


def round_qty(symbol: str, qty: float) -> float:
    """LOT_SIZE 对齐:stepSize 步进(半进位)+ minQty 门槛。不满足 ⇒ 0(拒单)。"""
    from decimal import Decimal, ROUND_HALF_UP

    f = filters_for(symbol)
    step = float(f.get("step_size") or 0.0)
    minq = float(f.get("min_qty") or 0.0)
    q = float(qty or 0.0)
    if q <= 0:
        return 0.0
    if step > 0:
        q = float((Decimal(str(q)) / Decimal(str(step))).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP) * Decimal(str(step)))
    if minq > 0 and q + 1e-12 < minq:
        return 0.0
    return q


def round_px(symbol: str, px: float) -> float:
    """PRICE_FILTER 对齐(tickSize,半进位——与交易所口径一致)。"""
    from decimal import Decimal, ROUND_HALF_UP

    f = filters_for(symbol)
    tick = float(f.get("tick_size") or 0.0)
    p = float(px or 0.0)
    if p <= 0 or tick <= 0:
        return p
    return float(Decimal(str(p)).quantize(Decimal(str(tick)),
                                          rounding=ROUND_HALF_UP))


def passes(symbol: str, px: float, qty: float, mark_px: float,
           reduce_only: bool = False) -> tuple:
    """(ok, reason)。MIN_NOTIONAL + PERCENT_PRICE 检查(与官方同口径)。"""
    f = filters_for(symbol)
    p, q = float(px or 0.0), float(qty or 0.0)
    if p <= 0 or q <= 0:
        return False, "zero_px_or_qty"
    if not reduce_only:
        mn = float(f.get("min_notional") or 0.0)
        if mn > 0 and p * q + 1e-9 < mn:
            return False, f"min_notional({p * q:.2f}<{mn})"
    if mark_px > 0:
        up = float(f.get("pct_mult_up") or 0.0)
        down = float(f.get("pct_mult_down") or 0.0)
        if up > 0 and p > mark_px * up:
            return False, f"pct_band_up({p:.6f}>{mark_px * up:.6f})"
        if down > 0 and p < mark_px * down:
            return False, f"pct_band_down({p:.6f}<{mark_px * down:.6f})"
    return True, ""


def quote_ops(old_bid: float, old_ask: float, new_bid: float, new_ask: float) -> int:
    """撤改才计单。只看盘、价格没变，计 0。换价是撤一张再挂一张，计 2。"""
    def same(x: float, y: float) -> bool:
        x, y = float(x or 0.0), float(y or 0.0)
        if x <= 0 and y <= 0:
            return True
        if x <= 0 or y <= 0:
            return False
        return abs(x - y) <= max(x, y) * 1e-8

    if same(old_bid, new_bid) and same(old_ask, new_ask):
        return 0
    had = float(old_bid or 0.0) > 0 or float(old_ask or 0.0) > 0
    has = float(new_bid or 0.0) > 0 or float(new_ask or 0.0) > 0
    if had and has:
        return 2
    if had or has:
        return 1
    return 0


class OrderRateLimit:
    """[h667] 订单操作速率模拟(1200 单/分/账户):撤+挂各计 1 op。
    超限 ⇒ 返回 False(= 模拟 429),调用方跳过本 tick 的撤改。"""

    def __init__(self, max_per_min: int = MAX_ORDERS_PER_MIN):
        self.max_per_min = int(max_per_min)
        self._window: list = []

    def allow(self, ops: int = 1, now: Optional[float] = None) -> bool:
        now = float(now or time.time())
        self._window = [t for t in self._window if now - t < 60.0]
        if len(self._window) + ops > self.max_per_min:
            return False
        self._window.extend([now] * ops)
        return True

    @property
    def used(self) -> int:
        now = time.time()
        self._window = [t for t in self._window if now - t < 60.0]
        return len(self._window)
