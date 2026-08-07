"""
多交易所时间对齐（P2.6b，方案 §P2.6b）。

目标：解决诊断'各 adapter 各自缓存，跨所时间戳漂移，横截面因子受污染'。

设计：
    - 各交易所快照按 watermark（最慢所时间戳）对齐
    - 漂移超阈 → 上报 DataQualityFlag（QualityGate 消费）
    - 用于横截面因子（多所同时间点数据才有效）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DriftFlag:
    """漂移告警。"""
    venue: str
    drift_ms: float
    detail: str


class TimeAligner:
    """
    多交易所时间对齐器。

    用法：
        aligner = TimeAligner(max_drift_ms=100)
        aligner.update("binance", ts)
        aligner.update("bybit", ts)
        if aligner.is_aligned():
            wm = aligner.watermark()  # 保守基线
    """

    def __init__(self, max_drift_ms: float = 100.0):
        self.max_drift_ms = max_drift_ms
        self._timestamps: dict[str, float] = {}

    def update(self, venue: str, ts_seconds: float) -> Optional[DriftFlag]:
        """
        更新某所时间戳。返回漂移告警（超阈）或 None。

        ts_seconds: 该所最新数据的秒级时间戳（venue exchange ts）。
        """
        self._timestamps[venue] = ts_seconds
        if len(self._timestamps) < 2:
            return None
        wm = self.watermark()
        drift = (ts_seconds - wm) * 1000.0  # ms
        if abs(drift) > self.max_drift_ms:
            return DriftFlag(
                venue=venue, drift_ms=drift,
                detail=f"{venue} 漂移 {drift:.1f}ms > {self.max_drift_ms}ms",
            )
        return None

    def watermark(self) -> Optional[float]:
        """最慢所的时间戳（保守对齐基线）。"""
        if not self._timestamps:
            return None
        return min(self._timestamps.values())

    def is_aligned(self) -> bool:
        """所有所时间戳是否在对齐阈值内。"""
        if len(self._timestamps) < 2:
            return True
        vals = list(self._timestamps.values())
        drift = (max(vals) - min(vals)) * 1000.0
        return drift <= self.max_drift_ms

    def reset(self) -> None:
        self._timestamps.clear()
