"""
DriftWatcher — 概念漂移检测 + adapt 闭环（P4.2，方案 §P2.4/§4.6）。

目标（方案环境7缺陷）：把 drift→adapt 串成自动闭环。
    监控模型误差流 → ADWIN（突变）/ Page-Hinkley（渐变）检出 drift
    → 触发 adapt（在线权重重置/regime 切换/MAML few-step/SHADOW 新候选）
    → 持续 drift 未消解 → ROLLBACK

无依赖设计：纯 numpy 实现的 ADWIN + Page-Hinkley（River 可选加速，缺失则降级）。
这是 drift→adapt state machine 的核心调度器。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ==================== 纯 numpy 漂移检测器（无依赖） ====================

class _ADWINCore:
    """
    ADWIN（Adaptive Windowing）简化实现。

    维护变长窗口；当窗口两半均值差异显著时收缩（检出漂移）。
    Hoeffding 边界保证误报率低。
    """

    def __init__(self, delta: float = 0.002):
        self.delta = delta
        self._window: list[float] = []
        self._total: float = 0.0

    def update(self, value: float) -> bool:
        """添加一个样本，返回是否检出 drift。"""
        self._window.append(value)
        self._total += value
        return self._check_cut()

    def _check_cut(self) -> bool:
        n = len(self._window)
        if n < 10:
            return False
        # 尝试在中间切分，比较两半均值
        for split in range(n // 4, 3 * n // 4, max(1, n // 8)):
            w0 = self._window[:split]
            w1 = self._window[split:]
            n0, n1 = len(w0), len(w1)
            if n0 < 5 or n1 < 5:
                continue
            m0, m1 = float(np.mean(w0)), float(np.mean(w1))
            # Hoeffding 边界
            eps = math.sqrt(
                1.0 / (2 * math.log(2 / self.delta)) *
                (1.0 / n0 + 1.0 / n1)
            )
            if abs(m0 - m1) > eps:
                # 检出漂移：丢弃前半窗口
                old_total = sum(w0)
                self._window = self._window[split:]
                self._total -= old_total
                return True
        return False


class _PageHinkleyCore:
    """
    Page-Hinkley 检验（CUSUM + 遗忘因子）。
    检测均值渐变。
    """

    def __init__(self, threshold: float = 50.0, alpha: float = 0.9999):
        self.threshold = threshold
        self.alpha = alpha  # 遗忘因子
        self._cum_sum: float = 0.0
        self._min_cum: float = math.inf
        self._mean_est: float = 0.0
        self._n: int = 0

    def update(self, value: float) -> bool:
        self._n += 1
        # 在线均值估计
        self._mean_est += (value - self._mean_est) / self._n
        # 累积偏差
        self._cum_sum = self.alpha * self._cum_sum + (value - self._mean_est)
        self._min_cum = min(self._min_cum, self._cum_sum)
        # PH_t = cum_sum - min_cum，超阈检出
        ph = self._cum_sum - self._min_cum
        return ph > self.threshold


# ==================== DriftWatcher ====================

class DriftType(str, Enum):
    ABRUPT = "abrupt"      # 突变（ADWIN）
    GRADUAL = "gradual"    # 渐变（Page-Hinkley）
    NONE = "none"


@dataclass
class DriftEvent:
    """漂移事件。"""
    ts_ns: int
    drift_type: DriftType
    metric_name: str
    current_value: float
    baseline_value: float
    severity: str = "WARN"   # WARN / CRITICAL


class AdaptStrategy(str, Enum):
    """适应策略（drift 触发后可采取）。"""
    ONLINE_WEIGHT_RESET = "online_weight_reset"   # River 在线权重重置
    REGIME_SWITCH = "regime_switch"               # 切 regime + 切子策略
    MAML_ADAPT = "maml_adapt"                     # few-step adapt
    SHADOW_NEW_CANDIDATE = "shadow_new_candidate" # 训练新候选
    ROLLBACK = "rollback"                         # 回滚前任（持续 drift 未消解）


@dataclass
class DriftWatcherConfig:
    """DriftWatcher 配置。"""
    adwin_delta: float = 0.002
    ph_threshold: float = 50.0
    ph_alpha: float = 0.9999
    min_samples_before_detect: int = 20
    consecutive_drifts_to_rollback: int = 3
    # 适应策略优先级（依次尝试）
    adapt_priority: tuple[AdaptStrategy, ...] = (
        AdaptStrategy.ONLINE_WEIGHT_RESET,
        AdaptStrategy.REGIME_SWITCH,
        AdaptStrategy.MAML_ADAPT,
        AdaptStrategy.SHADOW_NEW_CANDIDATE,
    )


class DriftWatcher:
    """
    概念漂移检测 + adapt 闭环调度器。

    用法：
        watcher = DriftWatcher()
        # 喂模型误差流
        drift = watcher.observe_error("sharpe", current_sharpe, baseline_sharpe)
        if drift:
            strategy = watcher.next_adapt_strategy()
            # 执行 adapt...
            if watcher.should_rollback():
                # ROLLBACK
    """

    def __init__(self, config: DriftWatcherConfig | None = None):
        self.config = config or DriftWatcherConfig()
        # 每个 metric 一组检测器
        self._adwins: dict[str, _ADWINCore] = {}
        self._phs: dict[str, _PageHinkleyCore] = {}
        self._sample_counts: dict[str, int] = {}
        self._consecutive_drifts: dict[str, int] = {}
        self._adapt_cursor: dict[str, int] = {}
        self.events: list[DriftEvent] = []
        # 尝试 import river（可选加速）
        self._has_river = False
        try:
            from river import drift as _river_drift  # noqa: F401
            self._has_river = True
        except ImportError:
            pass

    def observe_error(
        self, metric_name: str, value: float, baseline: Optional[float] = None,
    ) -> Optional[DriftEvent]:
        """
        观察一次模型误差/性能指标。返回 DriftEvent（检出漂移时）或 None。

        value: 当前指标值（如实时 Sharpe、IC、误差率）。
        baseline: 基线值（如历史均值）；None 则用累积均值。
        """
        self._sample_counts[metric_name] = self._sample_counts.get(metric_name, 0) + 1
        if self._sample_counts[metric_name] < self.config.min_samples_before_detect:
            return None

        # 偏差信号 = |value - baseline|
        if baseline is not None:
            signal = abs(value - baseline)
        else:
            signal = value

        # ADWIN（突变）
        if metric_name not in self._adwins:
            self._adwins[metric_name] = _ADWINCore(self.config.adwin_delta)
            self._phs[metric_name] = _PageHinkleyCore(
                self.config.ph_threshold, self.config.ph_alpha)

        abrupt = self._adwins[metric_name].update(signal)
        gradual = self._phs[metric_name].update(signal)

        import time
        if abrupt:
            self._consecutive_drifts[metric_name] = self._consecutive_drifts.get(metric_name, 0) + 1
            evt = DriftEvent(
                ts_ns=time.time_ns(), drift_type=DriftType.ABRUPT,
                metric_name=metric_name, current_value=value,
                baseline_value=baseline or 0.0,
                severity="CRITICAL" if self._consecutive_drifts[metric_name] >= 2 else "WARN",
            )
            self.events.append(evt)
            logger.warning(f"[DriftWatcher] 突变漂移 {metric_name}: {evt}")
            return evt
        if gradual:
            self._consecutive_drifts[metric_name] = self._consecutive_drifts.get(metric_name, 0) + 1
            evt = DriftEvent(
                ts_ns=time.time_ns(), drift_type=DriftType.GRADUAL,
                metric_name=metric_name, current_value=value,
                baseline_value=baseline or 0.0,
            )
            self.events.append(evt)
            logger.info(f"[DriftWatcher] 渐变漂移 {metric_name}: {evt}")
            return evt
        # 无漂移，重置计数
        self._consecutive_drifts[metric_name] = 0
        return None

    def next_adapt_strategy(self, metric_name: str) -> Optional[AdaptStrategy]:
        """
        返回下一个适应策略（按优先级依次尝试）。
        持续 drift → ROLLBACK。
        """
        if self.should_rollback(metric_name):
            return AdaptStrategy.ROLLBACK
        cursor = self._adapt_cursor.get(metric_name, 0)
        priority = self.config.adapt_priority
        if cursor >= len(priority):
            return None
        strategy = priority[cursor]
        self._adapt_cursor[metric_name] = cursor + 1
        return strategy

    def should_rollback(self, metric_name: str) -> bool:
        """连续漂移超阈 → 应回滚。"""
        return self._consecutive_drifts.get(metric_name, 0) >= self.config.consecutive_drifts_to_rollback

    def reset_adapt_cursor(self, metric_name: str) -> None:
        """adapt 成功消解 drift 后重置游标。"""
        self._adapt_cursor[metric_name] = 0
        self._consecutive_drifts[metric_name] = 0

    def stats(self) -> dict:
        return {
            "metrics_tracked": list(self._sample_counts.keys()),
            "events": len(self.events),
            "consecutive_drifts": dict(self._consecutive_drifts),
            "has_river": self._has_river,
        }
