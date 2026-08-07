"""
成交量异动检测服务

使用 z-score 方法检测成交量异常，分类为:
- volume_spike: 成交量激增 (>3σ)
- climax_volume: 极端成交量 (>4σ)
- volume_dry_up: 成交量枯竭 (<-2σ)
- accumulation: 价格窄幅震荡 + 成交量放大
- distribution: 价格窄幅区间 + 成交量异常（无趋势方向）
"""

from __future__ import annotations

import math
import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

Z_SCORE_THRESHOLD_SPIKE = 2.5
Z_SCORE_THRESHOLD_CLIMAX = 3.5
Z_SCORE_DRY_UP = -1.8
LOOKBACK_WINDOW = 50  # z-score 回顾窗口


@dataclass
class VolumeAnomaly:
    """成交量异动事件"""
    timestamp: int
    symbol: str
    anomaly_type: str         # volume_spike | climax_volume | volume_dry_up | accumulation | distribution
    volume: float
    volume_zscore: float      # z-score 值
    avg_volume: float         # 回顾窗口平均成交量
    price: float
    price_change_pct: float   # 相对于前一 K 线的价格变化
    severity: str             # "high" | "medium" | "low"
    description: str


class VolumeAnomalyDetector:
    """成交量异动检测器，基于滑动窗口 z-score"""

    def __init__(self, lookback: int = LOOKBACK_WINDOW):
        self.lookback = lookback

    def detect(self, klines: List[Dict[str, Any]], symbol: str = "") -> List[VolumeAnomaly]:
        """
        检测 K 线序列中的成交量异动。

        Args:
            klines: K 线数据 (按时间升序)
            symbol: 交易对名称

        Returns:
            检测到的异动列表
        """
        if len(klines) < self.lookback:
            return []

        volumes = [float(b["volume"]) for b in klines]
        closes = [float(b["close"]) for b in klines]
        highs = [float(b["high"]) for b in klines]
        lows = [float(b["low"]) for b in klines]
        timestamps = [int(b["timestamp"]) for b in klines]

        anomalies: List[VolumeAnomaly] = []

        for i in range(self.lookback, len(klines)):
            window = volumes[i - self.lookback:i]
            mean_vol = sum(window) / len(window)
            std_vol = _std(window, mean_vol)

            if std_vol < 1e-10:
                continue  # 成交量太平稳，无异常

            zscore = (volumes[i] - mean_vol) / std_vol
            current_price = closes[i]
            prev_price = closes[i - 1] if i > 0 else current_price
            price_change = ((current_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0

            anomaly = None

            # 成交量枯竭
            if zscore <= Z_SCORE_DRY_UP:
                anomaly = VolumeAnomaly(
                    timestamp=timestamps[i],
                    symbol=symbol,
                    anomaly_type="volume_dry_up",
                    volume=volumes[i],
                    volume_zscore=round(zscore, 2),
                    avg_volume=round(mean_vol, 2),
                    price=current_price,
                    price_change_pct=round(price_change, 2),
                    severity="medium",
                    description=f"成交量极度萎缩 (z={zscore:.1f}), 市场交投清淡",
                )

            # 极端成交量
            elif zscore >= Z_SCORE_THRESHOLD_CLIMAX:
                anomaly = VolumeAnomaly(
                    timestamp=timestamps[i],
                    symbol=symbol,
                    anomaly_type="climax_volume",
                    volume=volumes[i],
                    volume_zscore=round(zscore, 2),
                    avg_volume=round(mean_vol, 2),
                    price=current_price,
                    price_change_pct=round(price_change, 2),
                    severity="high",
                    description=f"极端成交量暴增 (z={zscore:.1f}), 可能伴随趋势转折",
                )

            # 成交量激增
            elif zscore >= Z_SCORE_THRESHOLD_SPIKE:
                # 进一步判断: 窄幅震荡 + 放量 = accumulation
                range_pct = _range_pct(highs[i], lows[i], closes[i])
                if range_pct < 1.0 and abs(price_change) < 0.5:
                    anomaly = VolumeAnomaly(
                        timestamp=timestamps[i],
                        symbol=symbol,
                        anomaly_type="accumulation",
                        volume=volumes[i],
                        volume_zscore=round(zscore, 2),
                        avg_volume=round(mean_vol, 2),
                        price=current_price,
                        price_change_pct=round(price_change, 2),
                        severity="medium",
                        description=f"窄幅放量 (z={zscore:.1f}), 主力可能在吸筹",
                    )
                else:
                    anomaly = VolumeAnomaly(
                        timestamp=timestamps[i],
                        symbol=symbol,
                        anomaly_type="volume_spike",
                        volume=volumes[i],
                        volume_zscore=round(zscore, 2),
                        avg_volume=round(mean_vol, 2),
                        price=current_price,
                        price_change_pct=round(price_change, 2),
                        severity="medium",
                        description=f"成交量显著放大 (z={zscore:.1f}), 关注后续走势",
                    )

            if anomaly:
                anomalies.append(anomaly)

        return anomalies

    def get_summary(self, anomalies: List[VolumeAnomaly]) -> Dict[str, Any]:
        """生成异动摘要"""
        if not anomalies:
            return {"has_anomalies": False, "count": 0, "types": {}, "latest": None}

        types = {}
        for a in anomalies:
            types[a.anomaly_type] = types.get(a.anomaly_type, 0) + 1

        latest = anomalies[-1] if anomalies else None
        high_count = sum(1 for a in anomalies if a.severity == "high")

        return {
            "has_anomalies": True,
            "count": len(anomalies),
            "high_severity_count": high_count,
            "types": types,
            "latest": {
                "timestamp": latest.timestamp,
                "type": latest.anomaly_type,
                "severity": latest.severity,
                "description": latest.description,
            } if latest else None,
        }


def _std(values: List[float], mean: float) -> float:
    """计算标准差"""
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _range_pct(high: float, low: float, close: float) -> float:
    """计算振幅百分比"""
    if close <= 0:
        return 0
    return ((high - low) / close) * 100


# 全局检测器
volume_detector = VolumeAnomalyDetector()
