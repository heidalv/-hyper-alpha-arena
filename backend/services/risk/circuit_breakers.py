# -*- coding: utf-8 -*-
"""熔断器集合（v3 方向 7）：连通性、闪崩、组合回撤。全部是“确定性算法核心 + 可单测的纯函数”。

1. ConnectivityBreaker —— 每 venue 连续失败计数：
     record_failure(venue) / record_success(venue)
     连续失败 ≥ RISK_CONNECTIVITY_FAIL_THRESHOLD（默认 3）→ 该 venue 禁开
     RISK_CONNECTIVITY_COOLDOWN_SEC（默认 600s）内无成功调用则维持禁开；成功一次即复位。
   接线：trading_commands._place_order_fresh_client（真实下单）与 ExchangeManager.check_health。

2. flash_crash_check(closes_1h, thresholds) —— 纯函数：
     BTC 1h 跌幅 ≥ 8% 或 4h 跌幅 ≥ 12% → 触发（REDUCING 24h）。
   evaluate_flash_crash() 从 K 线服务取 BTC 1h 收盘序列后调用纯函数。

3. drawdown_tier(equity, peak) —— 纯函数：
     回撤 < 20%  → scale 1.0, state ACTIVE
     20% ≤ dd < 30% → scale 0.5（各桶减半）, state ACTIVE
     dd ≥ 30%     → scale 0.0, state REDUCING（只平不开）
   阈值可用 RISK_DD_HALVE_PCT / RISK_DD_REDUCING_PCT 覆盖。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════════════
# 1. 连通性熔断
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class VenueHealth:
    venue: str
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_ts: Optional[float] = None
    last_success_ts: Optional[float] = None
    last_error: str = ""
    tripped_since: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "venue": self.venue,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "last_failure_ts": self.last_failure_ts,
            "last_success_ts": self.last_success_ts,
            "last_error": self.last_error,
            "tripped_since": self.tripped_since,
        }


class ConnectivityBreaker:
    def __init__(self, threshold: Optional[int] = None, cooldown_sec: Optional[float] = None):
        self._lock = threading.Lock()
        self._venues: Dict[str, VenueHealth] = {}
        self._threshold = threshold
        self._cooldown = cooldown_sec

    @property
    def threshold(self) -> int:
        return self._threshold if self._threshold is not None else max(1, _env_int("RISK_CONNECTIVITY_FAIL_THRESHOLD", 3))

    @property
    def cooldown(self) -> float:
        return self._cooldown if self._cooldown is not None else max(0.0, _env_float("RISK_CONNECTIVITY_COOLDOWN_SEC", 600.0))

    @staticmethod
    def _key(venue: Optional[str]) -> str:
        return str(venue or "unknown").strip().lower()

    def _get(self, venue: str) -> VenueHealth:
        k = self._key(venue)
        vh = self._venues.get(k)
        if vh is None:
            vh = VenueHealth(venue=k)
            self._venues[k] = vh
        return vh

    def record_failure(self, venue: str, error: str = "") -> VenueHealth:
        with self._lock:
            vh = self._get(venue)
            vh.consecutive_failures += 1
            vh.total_failures += 1
            vh.last_failure_ts = time.time()
            vh.last_error = str(error or "")[:200]
            if vh.consecutive_failures >= self.threshold and vh.tripped_since is None:
                vh.tripped_since = time.time()
                logger.error(
                    "[ConnectivityBreaker] venue=%s 连续失败 %d ≥ %d → 禁止新开仓 (cooldown %.0fs)",
                    vh.venue, vh.consecutive_failures, self.threshold, self.cooldown,
                )
                _alert_connectivity(vh, self.threshold)
            return vh

    def record_success(self, venue: str) -> VenueHealth:
        with self._lock:
            vh = self._get(venue)
            was_tripped = vh.tripped_since is not None
            vh.consecutive_failures = 0
            vh.total_successes += 1
            vh.last_success_ts = time.time()
            vh.tripped_since = None
            if was_tripped:
                logger.warning("[ConnectivityBreaker] venue=%s 恢复（成功调用）", vh.venue)
            return vh

    def is_tripped(self, venue: Optional[str]) -> bool:
        if venue is None:
            return False
        with self._lock:
            vh = self._venues.get(self._key(venue))
            if vh is None or vh.tripped_since is None:
                return False
            # 冷却期满且期间无新失败 → 自动放行一次探测（半开）
            if self.cooldown > 0 and time.time() - float(vh.last_failure_ts or vh.tripped_since) > self.cooldown:
                return False
            return True

    def tripped_venues(self) -> List[str]:
        with self._lock:
            keys = list(self._venues.keys())
        return [k for k in keys if self.is_tripped(k)]

    def status(self) -> Dict[str, Any]:
        with self._lock:
            venues = {k: v.to_dict() for k, v in self._venues.items()}
        for k in venues:
            venues[k]["tripped"] = self.is_tripped(k)
        return {"threshold": self.threshold, "cooldown_sec": self.cooldown, "venues": venues}

    def reset(self) -> None:
        with self._lock:
            self._venues.clear()


def _alert_connectivity(vh: VenueHealth, threshold: int) -> None:
    try:
        from backend.services.ops.alerts import send_alert
        send_alert(
            "P0", "交易所连通性熔断",
            f"venue: {vh.venue}\n连续失败: {vh.consecutive_failures} (阈值 {threshold})\n"
            f"最近错误: {vh.last_error}\n动作: 该 venue 禁止新开仓，平仓不受影响",
            dedupe_key=f"connectivity:{vh.venue}", source="connectivity_breaker",
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("[ConnectivityBreaker] 告警失败: %s", exc)


_connectivity: Optional[ConnectivityBreaker] = None


def get_connectivity_breaker() -> ConnectivityBreaker:
    global _connectivity
    if _connectivity is None:
        _connectivity = ConnectivityBreaker()
    return _connectivity


# ═══════════════════════════════════════════════════════════════════════
# 2. 闪崩熔断
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class FlashCrashVerdict:
    triggered: bool
    drop_1h_pct: Optional[float]
    drop_4h_pct: Optional[float]
    threshold_1h_pct: float
    threshold_4h_pct: float
    reason: str = ""
    last_price: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "triggered": self.triggered,
            "drop_1h_pct": self.drop_1h_pct, "drop_4h_pct": self.drop_4h_pct,
            "threshold_1h_pct": self.threshold_1h_pct, "threshold_4h_pct": self.threshold_4h_pct,
            "reason": self.reason, "last_price": self.last_price,
        }


def flash_crash_thresholds() -> Dict[str, float]:
    return {
        "1h": max(1.0, _env_float("RISK_FLASH_CRASH_1H_PCT", 8.0)),
        "4h": max(1.0, _env_float("RISK_FLASH_CRASH_4H_PCT", 12.0)),
        "ttl_hours": max(0.5, _env_float("RISK_FLASH_CRASH_REDUCING_HOURS", 24.0)),
    }


def flash_crash_check(closes_1h: Sequence[float], *, th_1h: Optional[float] = None,
                      th_4h: Optional[float] = None) -> FlashCrashVerdict:
    """纯函数。closes_1h：按时间升序的 BTC 1h 收盘价（最后一个=最新，可为进行中 K 线）。

    1h 跌幅 = last / closes[-2] - 1；4h 跌幅 = last / closes[-5] - 1。序列不足则对应项为 None。
    只看**下跌**（负向），上涨不触发。
    """
    th = flash_crash_thresholds()
    th1 = float(th_1h if th_1h is not None else th["1h"])
    th4 = float(th_4h if th_4h is not None else th["4h"])
    vals = [float(c) for c in closes_1h if c is not None and float(c) > 0]
    if len(vals) < 2:
        return FlashCrashVerdict(False, None, None, th1, th4, "insufficient_data", vals[-1] if vals else None)
    last = vals[-1]
    d1 = (last / vals[-2] - 1.0) * 100.0
    d4 = (last / vals[-5] - 1.0) * 100.0 if len(vals) >= 5 else None
    trig_1h = d1 <= -th1
    trig_4h = d4 is not None and d4 <= -th4
    reason = ""
    if trig_1h:
        reason = f"BTC 1h {d1:.2f}% ≤ -{th1:.0f}%"
    if trig_4h:
        reason = (reason + "; " if reason else "") + f"BTC 4h {d4:.2f}% ≤ -{th4:.0f}%"
    return FlashCrashVerdict(bool(trig_1h or trig_4h), round(d1, 3), (round(d4, 3) if d4 is not None else None),
                             th1, th4, reason, last)


def _btc_1h_closes(count: int = 8) -> List[float]:
    """取 BTC 1h 收盘序列（升序）。数据源：kline_service（active exchange 同源）。"""
    try:
        from backend.services.kline_data_service import kline_service
        rows = kline_service.get_klines_from_db("BTC", "1h", count=count) or []
    except Exception as exc:
        logger.debug("[FlashCrash] K 线读取失败: %s", exc)
        return []
    closes: List[float] = []
    for r in rows:
        c = None
        if isinstance(r, dict):
            c = r.get("close", r.get("c"))
        else:
            c = getattr(r, "close", None)
        try:
            if c is not None:
                closes.append(float(c))
        except (TypeError, ValueError):
            continue
    # 用最新现价替换最后一根（进行中 K 线的 close 可能滞后）
    try:
        from backend.services.market_price_service import get_price
        px = get_price("BTC")
        if px and float(px) > 0 and closes:
            closes[-1] = float(px)
    except Exception:
        pass
    return closes


def evaluate_flash_crash() -> FlashCrashVerdict:
    return flash_crash_check(_btc_1h_closes())


# ═══════════════════════════════════════════════════════════════════════
# 3. 组合回撤分级
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class DrawdownTier:
    drawdown_pct: float
    scale: float          # 1.0 / 0.5 / 0.0
    reducing: bool        # 是否应进入 REDUCING
    halve_pct: float
    reducing_pct: float
    label: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "drawdown_pct": round(self.drawdown_pct, 4), "scale": self.scale, "reducing": self.reducing,
            "halve_pct": self.halve_pct, "reducing_pct": self.reducing_pct, "label": self.label,
        }


def drawdown_tier(equity: float, peak: float, *, halve_pct: Optional[float] = None,
                  reducing_pct: Optional[float] = None) -> DrawdownTier:
    """纯函数：按组合回撤给出仓位缩放与是否只平不开。peak ≤ 0 视为无回撤。"""
    h = float(halve_pct if halve_pct is not None else _env_float("RISK_DD_HALVE_PCT", 20.0))
    r = float(reducing_pct if reducing_pct is not None else _env_float("RISK_DD_REDUCING_PCT", 30.0))
    if r < h:
        r = h
    if peak is None or float(peak) <= 0 or equity is None:
        return DrawdownTier(0.0, 1.0, False, h, r, "no_peak")
    dd = max(0.0, (float(peak) - float(equity)) / float(peak) * 100.0)
    if dd >= r:
        return DrawdownTier(dd, 0.0, True, h, r, "reducing")
    if dd >= h:
        return DrawdownTier(dd, 0.5, False, h, r, "halved")
    return DrawdownTier(dd, 1.0, False, h, r, "normal")
