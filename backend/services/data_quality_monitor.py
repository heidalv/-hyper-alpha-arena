"""
DataQualityMonitor — 数据质量监控

三大检测维度:
1. K线数据新鲜度: 每个 symbol 最新 K线时间戳 vs now (stale > 5min → 告警)
2. 因子值异常检测: 因子 z-score > 4 → 标记
3. 数据源可用性追踪: 成功率 + 平均延迟

集成位置: full_auto_trading_service._run_health_check 定期调用
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    level: str        # "warning" | "critical"
    source: str       # "kline_freshness" | "factor_anomaly" | "source_health"
    symbol: str
    message: str
    timestamp: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceHealthEntry:
    """Tracks success/failure stats for a single data source."""
    name: str
    total_calls: int = 0
    success_calls: int = 0
    total_latency_ms: float = 0.0
    last_success: float = 0.0
    last_failure: float = 0.0
    last_error: str = ""

    @property
    def success_rate(self) -> float:
        return self.success_calls / max(self.total_calls, 1)

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(self.success_calls, 1)


class DataQualityMonitor:
    """数据质量监控 — 检测数据延迟、缺失、异常"""

    STALE_THRESHOLD_SEC = 300        # 5min
    FACTOR_ZSCORE_THRESHOLD = 4.0
    SOURCE_HEALTH_WARNING = 0.7      # < 70% success → warning

    # v6 2.3 链路缺口阈值：last_success 距今超过即告警
    MARKET_GAP_SEC = 300             # 行情（ticker/quote）
    KLINE_GAP_SEC = 900              # K线（分钟级采集，15min 未成功即异常）
    ONCHAIN_GAP_SEC = 3600           # 链上（netflow/funding/stablecoin，小时级）

    def __init__(self):
        self._source_health: Dict[str, SourceHealthEntry] = {}
        self._alerts: List[Alert] = []
        self._max_alerts = 500

    # ── 链路分类（与前端 DataQualityPanel 归类一致） ──────────────────

    @staticmethod
    def _classify_source(name: str) -> str:
        n = name.lower()
        if any(k in n for k in ("ticker", "price", "market", "quote")):
            return "market"
        if "kline" in n:
            return "kline"
        if any(k in n for k in ("onchain", "chain", "whale", "netflow", "cvd", "funding", "coinglass", "stablecoin")):
            return "onchain"
        return ""

    # ── 链路缺口检测（v6 2.3 补行情/K线/链上缺口·延迟告警） ────────────

    def check_link_gaps(self) -> List[Alert]:
        """检测各链路数据源最后成功时间距今的缺口，超阈值生成 link_gap 告警。

        覆盖三种链路（按源名分类）：
          market  → MARKET_GAP_SEC（行情缺失 5min+）
          kline   → KLINE_GAP_SEC（K线采集 15min+ 无成功）
          onchain → ONCHAIN_GAP_SEC（链上/衍生品 1h+ 无成功）
        无成功记录（total_calls==0）的源跳过（视为未接入，不算缺口）。
        """
        alerts: List[Alert] = []
        now = time.time()
        gap_map = {"market": self.MARKET_GAP_SEC, "kline": self.KLINE_GAP_SEC, "onchain": self.ONCHAIN_GAP_SEC}
        for name, entry in self._source_health.items():
            if entry.total_calls == 0 or entry.last_success == 0:
                continue
            link = self._classify_source(name)
            threshold = gap_map.get(link)
            if not threshold:
                continue
            age = now - entry.last_success
            if age <= threshold:
                continue
            level = "critical" if age > threshold * 3 else "warning"
            alerts.append(Alert(
                level=level,
                source="link_gap",
                symbol="*",
                message=f"{link}链路数据源 '{name}' 最后成功 {age/60:.0f} 分钟前（>{threshold/60:.0f}分缺口）",
                details={
                    "link": link, "name": name, "gap_sec": round(age, 1),
                    "threshold_sec": threshold, "last_success": entry.last_success,
                },
            ))
        self._store_alerts(alerts)
        return alerts

    # ── 三链路健康视图（v6 2.3 统一看板） ────────────────────────────

    def get_link_health(self) -> Dict[str, Any]:
        """三链路（行情/K线/链上）健康视图 + DataProvider tier 状态。

        供 /api/monitor/data-quality 返回，前端 DataQualityPanel 链路卡消费：
        { market: {status, age_max_min, sources:[...]}, kline: {...},
          onchain: {...}, providers: {coinglass: {tier, ok, ...}} }
        """
        now = time.time()
        by_link: Dict[str, Dict[str, Any]] = {
            "market": {"sources": [], "age_max_min": None, "status": "ok"},
            "kline": {"sources": [], "age_max_min": None, "status": "ok"},
            "onchain": {"sources": [], "age_max_min": None, "status": "ok"},
        }
        for name, entry in self._source_health.items():
            link = self._classify_source(name)
            if not link or link not in by_link:
                continue
            age_min = round((now - entry.last_success) / 60, 1) if entry.last_success else None
            by_link[link]["sources"].append({
                "name": name,
                "total_calls": entry.total_calls,
                "success_rate": round(entry.success_rate, 3),
                "avg_latency_ms": round(entry.avg_latency_ms, 1),
                "last_success_min": age_min,
                "healthy": entry.last_success != 0 and entry.success_rate >= self.SOURCE_HEALTH_WARNING,
            })
        for link, info in by_link.items():
            if not info["sources"]:
                info["status"] = "n/a"
                continue
            ages = [s["last_success_min"] for s in info["sources"] if s["last_success_min"] is not None]
            if ages:
                info["age_max_min"] = max(ages)
            worst = min(s["healthy"] for s in info["sources"])
            info["status"] = "ok" if worst else "dead"

        # DataProvider 统一状态（Coinglass 免费/付费 tier）
        providers: Dict[str, Any] = {}
        try:
            from backend.services.data.data_provider import provider_chain
            providers = provider_chain.health_report()
        except Exception:
            pass

        return {**by_link, "providers": providers}

    # ── Public API ───────────────────────────────

    def check_kline_freshness(
        self,
        symbols: List[str],
        latest_timestamps: Dict[str, float],
    ) -> List[Alert]:
        """
        检查 K线数据新鲜度。

        Args:
            symbols: 交易对列表
            latest_timestamps: {symbol: unix_ts} 每个 symbol 最新K线时间

        Returns:
            告警列表
        """
        alerts: List[Alert] = []
        now = time.time()

        for sym in symbols:
            ts = latest_timestamps.get(sym)
            if ts is None:
                alerts.append(Alert(
                    level="critical",
                    source="kline_freshness",
                    symbol=sym,
                    message=f"No kline data for {sym}",
                ))
                continue

            age = now - ts
            if age > self.STALE_THRESHOLD_SEC * 2:
                alerts.append(Alert(
                    level="critical",
                    source="kline_freshness",
                    symbol=sym,
                    message=f"{sym} kline stale for {age:.0f}s (> 10min)",
                    details={"age_sec": age, "last_ts": ts},
                ))
            elif age > self.STALE_THRESHOLD_SEC:
                alerts.append(Alert(
                    level="warning",
                    source="kline_freshness",
                    symbol=sym,
                    message=f"{sym} kline stale for {age:.0f}s (> 5min)",
                    details={"age_sec": age, "last_ts": ts},
                ))

        self._store_alerts(alerts)
        return alerts

    def check_factor_anomalies(
        self,
        factor_values: Dict[str, Dict[str, float]],
    ) -> List[Alert]:
        """
        检测因子值异常 (z-score > threshold)。

        Args:
            factor_values: {symbol: {factor_id: value}}

        Returns:
            告警列表
        """
        alerts: List[Alert] = []

        # 收集每个因子跨所有 symbol 的值
        factor_pools: Dict[str, List[float]] = {}
        for sym, factors in factor_values.items():
            for fid, val in factors.items():
                if val is not None and np.isfinite(val):
                    factor_pools.setdefault(fid, []).append(val)

        # 计算 z-score
        factor_stats: Dict[str, tuple] = {}
        for fid, vals in factor_pools.items():
            if len(vals) >= 3:
                arr = np.array(vals)
                factor_stats[fid] = (float(np.mean(arr)), float(np.std(arr)))

        for sym, factors in factor_values.items():
            for fid, val in factors.items():
                if val is None or not np.isfinite(val):
                    continue
                stats = factor_stats.get(fid)
                if stats is None:
                    continue
                mean, std = stats
                if std < 1e-10:
                    continue
                z = abs(val - mean) / std
                if z > self.FACTOR_ZSCORE_THRESHOLD:
                    alerts.append(Alert(
                        level="warning",
                        source="factor_anomaly",
                        symbol=sym,
                        message=f"Factor {fid} z-score={z:.1f} for {sym}",
                        details={"factor_id": fid, "value": val, "z_score": z},
                    ))

        self._store_alerts(alerts)
        return alerts

    def get_source_health_report(self) -> Dict[str, Dict[str, Any]]:
        """返回各数据源的健康报告。"""
        report: Dict[str, Dict[str, Any]] = {}
        for name, entry in self._source_health.items():
            report[name] = {
                "total_calls": entry.total_calls,
                "success_rate": round(entry.success_rate, 3),
                "avg_latency_ms": round(entry.avg_latency_ms, 1),
                "last_success": entry.last_success,
                "last_failure": entry.last_failure,
                "last_error": entry.last_error,
                "healthy": entry.success_rate >= self.SOURCE_HEALTH_WARNING,
            }
        return report

    def check_source_health(self) -> List[Alert]:
        """对健康度 < 70% 的数据源生成告警。"""
        alerts: List[Alert] = []
        for name, entry in self._source_health.items():
            if entry.total_calls < 5:
                continue
            if entry.success_rate < self.SOURCE_HEALTH_WARNING:
                alerts.append(Alert(
                    level="warning",
                    source="source_health",
                    symbol="*",
                    message=f"Data source '{name}' success rate={entry.success_rate:.0%}",
                    details={
                        "name": name,
                        "success_rate": entry.success_rate,
                        "last_error": entry.last_error,
                    },
                ))
        self._store_alerts(alerts)
        return alerts

    # ── Source tracking API (for other collectors to call) ────

    def record_source_call(
        self, source_name: str, success: bool, latency_ms: float = 0.0, error: str = ""
    ):
        """Record a data source call result."""
        entry = self._source_health.get(source_name)
        if entry is None:
            entry = SourceHealthEntry(name=source_name)
            self._source_health[source_name] = entry

        entry.total_calls += 1
        if success:
            entry.success_calls += 1
            entry.total_latency_ms += latency_ms
            entry.last_success = time.time()
        else:
            entry.last_failure = time.time()
            entry.last_error = error

    # ── Recent alerts ────────────────────────────

    def get_recent_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent alerts as dicts."""
        return [
            {
                "level": a.level,
                "source": a.source,
                "symbol": a.symbol,
                "message": a.message,
                "timestamp": a.timestamp,
                "details": a.details,
            }
            for a in self._alerts[-limit:]
        ]

    # ── internals ────────────────────────────────

    def _store_alerts(self, alerts: List[Alert]):
        self._alerts.extend(alerts)
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]
        for a in alerts:
            log_fn = logger.warning if a.level == "critical" else logger.info
            log_fn(f"[DataQuality] {a.level}: {a.message}")


# Global singleton
_monitor: Optional[DataQualityMonitor] = None


def get_data_quality_monitor() -> DataQualityMonitor:
    global _monitor
    if _monitor is None:
        _monitor = DataQualityMonitor()
    return _monitor
