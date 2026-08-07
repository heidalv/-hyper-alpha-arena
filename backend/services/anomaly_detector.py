"""
AnomalyDetector — 异常检测引擎

纯 NumPy/Pandas 实现的异常检测，不依赖ML库。
检测类型：价格突刺、成交量激增、资金费率极端、OI背离、因子异常。

设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第5.2节
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
from enum import Enum

import numpy as np
import pandas as pd


class AnomalyType(Enum):
    PRICE_SPIKE = "price_spike"           # 价格突刺
    VOLUME_SURGE = "volume_surge"         # 成交量激增
    FUNDING_EXTREME = "funding_extreme"   # 资金费率极端
    OI_DIVERGENCE = "oi_divergence"       # OI背离
    FACTOR_ANOMALY = "factor_anomaly"     # 因子异常
    CORRELATION_BREAK = "corr_break"      # 相关性断裂


@dataclass
class AnomalyEvent:
    """异常事件"""
    event_id: str
    symbol: str
    anomaly_type: AnomalyType
    severity: float             # 0~1
    z_score: float              # Z-Score值
    description: str
    raw_value: float
    expected_range: tuple       # (下界, 上界)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_critical(self) -> bool:
        return self.severity > 0.8


@dataclass
class AnomalyReport:
    """异常检测报告"""
    symbol: str
    events: List[AnomalyEvent]
    total_anomaly_score: float
    recommended_action: str     # "investigate" / "alert" / "trade_opportunity"
    timestamp: datetime = field(default_factory=datetime.now)


class AnomalyDetector:
    """
    异常检测引擎

    纯 NumPy/Pandas 实现，不依赖ML库。
    使用 Z-Score 和 IQR 方法检测多种类型的异常。
    """

    PRICE_ZSCORE_THRESHOLD = 3.0
    VOLUME_ZSCORE_THRESHOLD = 2.5
    FACTOR_ZSCORE_THRESHOLD = 2.0
    FUNDING_EXTREME_THRESHOLD = 0.01   # 1%

    def detect(
        self,
        symbol: str,
        klines: pd.DataFrame,
        market_data: Dict,
        factor_signals: Optional[Dict] = None,
        lookback: int = 100,
    ) -> AnomalyReport:
        """对单个交易对执行全面异常检测"""
        events: List[AnomalyEvent] = []

        if klines is not None and not klines.empty:
            events.extend(self._detect_price_anomalies(symbol, klines, lookback))
            events.extend(self._detect_volume_anomalies(symbol, klines, lookback))

        if market_data:
            events.extend(self._detect_funding_anomalies(symbol, market_data))
            events.extend(self._detect_oi_anomalies(symbol, klines, market_data))

        if factor_signals:
            events.extend(self._detect_factor_anomalies(symbol, factor_signals))

        total_score = sum(e.severity for e in events) / max(len(events), 1)

        if any(e.is_critical for e in events):
            action = "alert"
        elif total_score > 0.5:
            action = "trade_opportunity"
        else:
            action = "investigate"

        return AnomalyReport(
            symbol=symbol,
            events=events,
            total_anomaly_score=total_score,
            recommended_action=action,
        )

    def _detect_price_anomalies(
        self, symbol: str, klines: pd.DataFrame, lookback: int
    ) -> List[AnomalyEvent]:
        events = []
        close = klines['close'].values

        if len(close) < lookback:
            return events

        ts = int(datetime.now().timestamp())

        # Z-Score方法
        hist = close[-lookback:]
        mean = float(np.mean(hist[:-1]))
        std = float(np.std(hist[:-1])) + 1e-10
        z = float((close[-1] - mean) / std)

        if abs(z) > self.PRICE_ZSCORE_THRESHOLD:
            events.append(AnomalyEvent(
                event_id=f"price_{symbol}_{ts}",
                symbol=symbol,
                anomaly_type=AnomalyType.PRICE_SPIKE,
                severity=min(abs(z) / 5.0, 1.0),
                z_score=z,
                description=f"price_anomaly: Z={z:.2f}, current={close[-1]:.4f}, mean={mean:.4f}",
                raw_value=float(close[-1]),
                expected_range=(mean - 2 * std, mean + 2 * std),
            ))

        # IQR方法（对尾部风险更敏感）
        returns = np.diff(np.log(close[-lookback:]))
        q1, q3 = float(np.percentile(returns, 25)), float(np.percentile(returns, 75))
        iqr = q3 - q1
        latest_return = float(returns[-1])

        if latest_return > q3 + 2.5 * iqr or latest_return < q1 - 2.5 * iqr:
            events.append(AnomalyEvent(
                event_id=f"return_{symbol}_{ts}",
                symbol=symbol,
                anomaly_type=AnomalyType.PRICE_SPIKE,
                severity=0.8,
                z_score=z,
                description=f"return_anomaly(IQR): {latest_return:.4f}, range=[{q1-1.5*iqr:.4f}, {q3+1.5*iqr:.4f}]",
                raw_value=latest_return,
                expected_range=(q1 - 1.5 * iqr, q3 + 1.5 * iqr),
            ))

        return events

    def _detect_volume_anomalies(
        self, symbol: str, klines: pd.DataFrame, lookback: int
    ) -> List[AnomalyEvent]:
        events = []
        if 'volume' not in klines.columns:
            return events

        volume = klines['volume'].values
        if len(volume) < lookback:
            return events

        ts = int(datetime.now().timestamp())

        hist = volume[-lookback:-1]
        mean = float(np.mean(hist))
        std = float(np.std(hist)) + 1e-10
        z = float((volume[-1] - mean) / std)

        if z > self.VOLUME_ZSCORE_THRESHOLD:
            events.append(AnomalyEvent(
                event_id=f"vol_{symbol}_{ts}",
                symbol=symbol,
                anomaly_type=AnomalyType.VOLUME_SURGE,
                severity=min(z / 4.0, 1.0),
                z_score=z,
                description=f"volume_surge: Z={z:.2f}, current={volume[-1]:.0f}, mean={mean:.0f}",
                raw_value=float(volume[-1]),
                expected_range=(0, mean + 2 * std),
            ))

        return events

    def _detect_funding_anomalies(
        self, symbol: str, market_data: Dict
    ) -> List[AnomalyEvent]:
        events = []
        rate = float(market_data.get('funding_rate', 0))

        if abs(rate) > self.FUNDING_EXTREME_THRESHOLD:
            ts = int(datetime.now().timestamp())
            events.append(AnomalyEvent(
                event_id=f"fund_{symbol}_{ts}",
                symbol=symbol,
                anomaly_type=AnomalyType.FUNDING_EXTREME,
                severity=min(abs(rate) / 0.03, 1.0),
                z_score=rate / 0.01,
                description=f"funding_extreme: {rate:.4%}",
                raw_value=rate,
                expected_range=(-0.01, 0.01),
            ))

        return events

    def _detect_oi_anomalies(
        self, symbol: str, klines: Optional[pd.DataFrame], market_data: Dict
    ) -> List[AnomalyEvent]:
        events = []
        if klines is None or 'oi' not in klines.columns:
            return events

        close = klines['close'].values
        oi = klines['oi'].values
        if len(oi) < 24 or len(close) < 24:
            return events

        # OI和价格背离
        price_change = float((close[-1] - close[-24]) / close[-24])
        oi_change = float((oi[-1] - oi[-24]) / (oi[-24] + 1e-10))

        # 价格下跌但OI上升 = 空头积累信号
        if price_change < -0.05 and oi_change > 0.1:
            ts = int(datetime.now().timestamp())
            events.append(AnomalyEvent(
                event_id=f"oi_div_{symbol}_{ts}",
                symbol=symbol,
                anomaly_type=AnomalyType.OI_DIVERGENCE,
                severity=0.7,
                z_score=oi_change / 0.05,
                description=f"oi_divergence: price={price_change:.1%}, oi={oi_change:.1%}",
                raw_value=oi_change,
                expected_range=(-0.05, 0.05),
            ))

        return events

    def _detect_factor_anomalies(
        self, symbol: str, factor_signals: Dict
    ) -> List[AnomalyEvent]:
        events = []
        ts = int(datetime.now().timestamp())

        for factor_id, signal in factor_signals.items():
            if hasattr(signal, 'z_score') and abs(signal.z_score) > self.FACTOR_ZSCORE_THRESHOLD:
                raw = float(getattr(signal, 'raw_value', 0))
                events.append(AnomalyEvent(
                    event_id=f"factor_{symbol}_{factor_id}_{ts}",
                    symbol=symbol,
                    anomaly_type=AnomalyType.FACTOR_ANOMALY,
                    severity=min(abs(signal.z_score) / 4.0, 1.0),
                    z_score=signal.z_score,
                    description=f"factor_{factor_id}_anomaly: Z={signal.z_score:.2f}",
                    raw_value=raw,
                    expected_range=(-2, 2),
                ))

        return events
