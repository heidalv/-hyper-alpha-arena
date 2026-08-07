"""
ArbitrageAlertMonitor — 套利专用监控告警

覆盖：单腿失败、资金池耗尽、资金费率突变、熔断器激活。
接入 AlertSystem（日志 + 可选钉钉）。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ArbAlert:
    alert_id: str
    level: str          # info | warning | critical
    code: str           # leg_failure | pool_exhaustion | funding_spike | circuit_breaker
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class ArbitrageAlertMonitor:
    """套利告警收集与分发"""

    POOL_UTILIZATION_WARN = 0.85
    POOL_UTILIZATION_CRITICAL = 0.95
    FUNDING_SPIKE_THRESHOLD = 0.001   # 0.1% 单期费率
    COOLDOWN_SEC = 300

    def __init__(self, max_history: int = 500):
        self._alerts: Deque[ArbAlert] = deque(maxlen=max_history)
        self._lock = threading.Lock()
        self._last_emit: Dict[str, float] = {}

    def _should_emit(self, code: str) -> bool:
        now = time.time()
        last = self._last_emit.get(code, 0)
        if now - last < self.COOLDOWN_SEC:
            return False
        self._last_emit[code] = now
        return True

    def emit(
        self,
        level: str,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Optional[ArbAlert]:
        if not force and not self._should_emit(code):
            return None

        alert = ArbAlert(
            alert_id=f"arb_{uuid.uuid4().hex[:10]}",
            level=level,
            code=code,
            message=message,
            details=details or {},
        )
        with self._lock:
            self._alerts.append(alert)

        log_fn = logger.warning if level in ("warning", "critical") else logger.info
        log_fn("[ArbAlert] [%s] %s — %s", level.upper(), code, message)

        self._push_to_alert_system(alert)
        return alert

    def _push_to_alert_system(self, alert: ArbAlert) -> None:
        try:
            from backend.services.monitoring.alert_system import (
                AlertCategory,
                AlertChannel,
                AlertLevel,
                get_alert_system,
            )
            level_map = {
                "info": AlertLevel.INFO,
                "warning": AlertLevel.WARNING,
                "critical": AlertLevel.CRITICAL,
            }
            get_alert_system().trigger_manual_alert(
                account_id=0,
                level=level_map.get(alert.level, AlertLevel.WARNING),
                message=f"[套利/{alert.code}] {alert.message}",
                category=AlertCategory.TRADE,
                channels=[AlertChannel.LOG],
            )
        except Exception as e:
            logger.debug("[ArbAlert] AlertSystem push failed: %s", e)

    def on_leg_failure(
        self,
        symbol: str,
        exchange: str,
        error: str,
        leg: str = "unknown",
    ) -> None:
        self.emit(
            "critical",
            "leg_failure",
            f"单腿失败 {leg}: {exchange} {symbol} — {error}",
            {"symbol": symbol, "exchange": exchange, "leg": leg, "error": error},
            force=True,
        )

    def check_pool_utilization(self, global_status: Dict[str, Any]) -> None:
        allocations = global_status.get("allocations", {})
        used = global_status.get("used", {})
        for pool, allocated in allocations.items():
            if pool == "emergency_reserve" or allocated <= 0:
                continue
            util = used.get(pool, 0) / allocated
            if util >= self.POOL_UTILIZATION_CRITICAL:
                self.emit(
                    "critical",
                    "pool_exhaustion",
                    f"资金池 {pool} 使用率 {util:.0%}（临界）",
                    {"pool": pool, "utilization": util, "allocated": allocated, "used": used.get(pool, 0)},
                )
            elif util >= self.POOL_UTILIZATION_WARN:
                self.emit(
                    "warning",
                    "pool_low",
                    f"资金池 {pool} 使用率 {util:.0%}（偏低）",
                    {"pool": pool, "utilization": util},
                )

    def check_funding_spikes(self, funding_rates: Dict[str, float]) -> None:
        for symbol, rate in funding_rates.items():
            if abs(rate) >= self.FUNDING_SPIKE_THRESHOLD:
                self.emit(
                    "warning",
                    "funding_spike",
                    f"{symbol} 资金费率异常: {rate:.4%}",
                    {"symbol": symbol, "rate": rate},
                )

    def on_circuit_breaker(self, reason: str) -> None:
        self.emit(
            "critical",
            "circuit_breaker",
            f"套利熔断器激活: {reason}",
            {"reason": reason},
            force=True,
        )

    def get_alerts(
        self,
        since: float = 0.0,
        limit: int = 50,
        code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            items = [a for a in self._alerts if a.timestamp > since]
            if code:
                items = [a for a in items if a.code == code]
            items = items[-limit:]
            return [
                {
                    "alert_id": a.alert_id,
                    "level": a.level,
                    "code": a.code,
                    "message": a.message,
                    "details": a.details,
                    "timestamp": a.timestamp,
                }
                for a in items
            ]

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            by_level: Dict[str, int] = {}
            by_code: Dict[str, int] = {}
            for a in self._alerts:
                by_level[a.level] = by_level.get(a.level, 0) + 1
                by_code[a.code] = by_code.get(a.code, 0) + 1
            return {
                "total": len(self._alerts),
                "by_level": by_level,
                "by_code": by_code,
                "latest": self.get_alerts(limit=5),
            }


arb_alert_monitor = ArbitrageAlertMonitor()
