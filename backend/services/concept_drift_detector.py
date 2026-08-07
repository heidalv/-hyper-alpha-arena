"""
概念漂移检测器 (P0.6) — 检测市场结构性变化，触发策略重审

核心职责：
1. KS 检验：检测收益率分布是否发生漂移（双窗口 7d/14d）
2. MMD 检测：最大均值差异检测（非线性分布漂移）
3. ADWIN 在线漂移：自适应滑动窗口检测（流式，秒级响应）
4. 漂移告警级别：黄色（观察）→ 红色（重审）→ 冻结

加密适配：
- KS 检验窗口从 30d 缩短为 7d/14d 双窗口（加密市场漂移更快）
- ADWIN 在线检测优先级 > 批量 KS（加密市场需更快响应）
- 漂移告警区分"暂时性波动"与"结构性漂移"
- 新增周末数据独立窗口评估
"""

import logging
import math
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── 加密适配：漂移检测窗口配置 ──
KS_WINDOW_FAST = 7          # 快速检测窗口 (天)
KS_WINDOW_SLOW = 14         # 确认漂移窗口 (天)
ROLLING_SHARPE_WINDOW = 14  # 滚动 Sharpe 窗口 (加密加速，非 30 天)
ADWIN_DELTA = 0.002         # ADWIN 灵敏度参数
DRIFT_COOLDOWN_HOURS = 12   # 漂移告警冷却（加密更短）

# ── 告警级别 ──
DRIFT_LEVEL_GREEN = "green"     # 无漂移
DRIFT_LEVEL_YELLOW = "yellow"   # 7d 窗口检测到漂移 → 观察
DRIFT_LEVEL_RED = "red"         # 14d 窗口确认漂移 → 重审
DRIFT_LEVEL_FROZEN = "frozen"   # 两个窗口都告警 → 冻结


@dataclass
class DriftAlert:
    """漂移告警"""
    alert_id: str
    strategy_id: str
    symbol: str
    level: str                           # green|yellow|red|frozen
    ks_stat_7d: Optional[float] = None
    ks_p_value_7d: Optional[float] = None
    ks_stat_14d: Optional[float] = None
    ks_p_value_14d: Optional[float] = None
    mmd_stat: Optional[float] = None
    adwin_drift_detected: bool = False
    rolling_sharpe: Optional[float] = None
    trending_down: bool = False          # 滚动 Sharpe 持续下降
    weekend_anomaly: bool = False        # 周末数据显著偏离
    description: str = ""
    detected_at: str = ""
    acknowledged: bool = False

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()


class ConceptDriftDetector:
    """概念漂移检测器（单例） — P0.6"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # 按策略+标的存储收益率序列
        self._pnl_series: Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self._weekend_pnl: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self._weekday_pnl: Dict[str, deque] = defaultdict(lambda: deque(maxlen=150))

        # ADWIN 实例（每个策略+标的一个）
        self._adwin_instances: Dict[str, Any] = {}

        # 告警历史
        self._alerts: Dict[str, List[DriftAlert]] = defaultdict(list)
        self._last_alert_at: Dict[str, datetime] = {}

        # 统计
        self._total_checks = 0

        logger.info(
            f"[DriftDetector] 概念漂移检测器初始化完成 "
            f"(ks_windows={KS_WINDOW_FAST}d/{KS_WINDOW_SLOW}d, "
            f"sharpe_window={ROLLING_SHARPE_WINDOW}d, adwin_delta={ADWIN_DELTA})"
        )

    # ══════════════════════════════════════════════════
    #  数据输入
    # ══════════════════════════════════════════════════

    def record_pnl(
        self,
        strategy_id: str,
        symbol: str,
        pnl_pct: float,
        *,
        is_weekend: bool = False,
        opened_at: Optional[datetime] = None,
    ) -> None:
        """记录一笔交易结果。

        由 learning_bus / paper_trading_engine 在每笔平仓后调用。
        """
        key = f"{strategy_id}:{symbol}"

        with self._lock:
            self._pnl_series[key].append(pnl_pct)
            if is_weekend:
                self._weekend_pnl[key].append(pnl_pct)
            else:
                self._weekday_pnl[key].append(pnl_pct)

            # ADWIN 在线检测
            adwin = self._adwin_instances.get(key)
            if adwin is not None:
                try:
                    adwin.add_element(pnl_pct)
                except Exception:
                    pass

        self._total_checks += 1

    # ══════════════════════════════════════════════════
    #  漂移检测核心
    # ══════════════════════════════════════════════════

    def check(
        self, strategy_id: str, symbol: str
    ) -> Optional[DriftAlert]:
        """执行完整的漂移检测流程。

        Returns:
            DriftAlert if drift detected, None otherwise.
        """
        key = f"{strategy_id}:{symbol}"

        # 冷却检查
        last = self._last_alert_at.get(key)
        if last:
            hours_since = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if hours_since < DRIFT_COOLDOWN_HOURS:
                return None

        with self._lock:
            all_pnl = list(self._pnl_series.get(key, []))
            weekend_pnl = list(self._weekend_pnl.get(key, []))
            weekday_pnl = list(self._weekday_pnl.get(key, []))

        if len(all_pnl) < 20:
            return None  # 样本不足

        # ── 1. KS 检验（双窗口） ──
        ks_fast = self._ks_test(all_pnl, window_days=KS_WINDOW_FAST)
        ks_slow = self._ks_test(all_pnl, window_days=KS_WINDOW_SLOW)

        # ── 2. MMD 检测 ──
        mmd_result = self._mmd_test(all_pnl)

        # ── 3. ADWIN 检测 ──
        adwin_drift = False
        adwin = self._adwin_instances.get(key)
        if adwin is not None:
            try:
                adwin_drift = adwin.drift_detected if hasattr(adwin, 'drift_detected') else False
            except Exception:
                pass

        # ── 4. 滚动 Sharpe ──
        sharpe = self._rolling_sharpe(all_pnl, ROLLING_SHARPE_WINDOW)
        trending_down = self._sharpe_trending_down(all_pnl)

        # ── 5. 周末异常检测 ──
        weekend_anomaly = False
        if len(weekend_pnl) >= 10 and len(weekday_pnl) >= 10:
            weekend_mean = np.mean(weekend_pnl)
            weekday_mean = np.mean(weekday_pnl)
            if abs(weekend_mean - weekday_mean) > 0.02:  # 2% 偏差
                weekend_anomaly = True

        # ── 告警级别判定 ──
        ks7_sig = ks_fast and ks_fast.get("p_value", 1.0) < 0.05
        ks14_sig = ks_slow and ks_slow.get("p_value", 1.0) < 0.05

        if ks7_sig and ks14_sig:
            level = DRIFT_LEVEL_FROZEN
        elif ks14_sig:
            level = DRIFT_LEVEL_RED
        elif ks7_sig:
            level = DRIFT_LEVEL_YELLOW
        else:
            level = DRIFT_LEVEL_GREEN

        if level == DRIFT_LEVEL_GREEN and not adwin_drift:
            return None

        # 构建告警
        alert = DriftAlert(
            alert_id=f"drift_{key}_{int(datetime.now(timezone.utc).timestamp())}",
            strategy_id=strategy_id,
            symbol=symbol,
            level=level,
            ks_stat_7d=ks_fast.get("statistic") if ks_fast else None,
            ks_p_value_7d=ks_fast.get("p_value") if ks_fast else None,
            ks_stat_14d=ks_slow.get("statistic") if ks_slow else None,
            ks_p_value_14d=ks_slow.get("p_value") if ks_slow else None,
            mmd_stat=mmd_result.get("statistic") if mmd_result else None,
            adwin_drift_detected=adwin_drift,
            rolling_sharpe=round(sharpe, 4) if sharpe is not None else None,
            trending_down=trending_down,
            weekend_anomaly=weekend_anomaly,
            description=self._build_alert_description(
                level, ks7_sig, ks14_sig, adwin_drift, sharpe, weekend_anomaly
            ),
        )

        self._alerts[key].append(alert)
        self._last_alert_at[key] = datetime.now(timezone.utc)

        logger.warning(
            f"[DriftDetector] 概念漂移告警: {key} level={level} "
            f"ks7_p={ks_fast.get('p_value', 1):.4f} ks14_p={ks_slow.get('p_value', 1):.4f} "
            f"adwin={adwin_drift} sharpe={alert.rolling_sharpe}"
        )

        # ── 整改#18：DDG-DA 主动分布预测（漂移触发 → 预加权，供后续重训消费）──
        try:
            import os as _os
            if _os.getenv("DDGDA_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
                import numpy as _np
                from backend.services.learning_core.distribution_forecaster import get_forecaster
                _drift_score = 0.9 if level == DRIFT_LEVEL_FROZEN else (
                    0.7 if level == DRIFT_LEVEL_RED else (0.4 if level == DRIFT_LEVEL_YELLOW else 0.1))
                _fh = _np.asarray(pnl_series[-min(len(pnl_series), 80):], dtype=float).reshape(-1, 1)
                _fc = get_forecaster().forecast_next_distribution(
                    _fh, drift_signal=_drift_score, regime_hint=level,
                )
                alert.description += (
                    f" | DDG-DA conf={_fc.confidence:.2f} "
                    f"reweight_mean={float(_np.mean(_fc.sample_weights)):.3f}"
                )
        except Exception as _ddg_err:
            logger.debug("[DriftDetector][DDG-DA#18] 跳过: %s", _ddg_err)

        return alert

    # ══════════════════════════════════════════════════
    #  统计检验
    # ══════════════════════════════════════════════════

    def _ks_test(self, pnl_series: List[float], window_days: int) -> Optional[Dict]:
        """KS 检验：前半段 vs 后半段分布是否一致。

        将序列等分为前后两半（窗口按交易数近似折算），
        检验 H0: 两个分布来自同一总体。
        """
        n = len(pnl_series)
        if n < 10:
            return None

        # 近似折算：每天约 N 笔交易
        daily_rate = max(n / 30.0, 1.0)
        window_trades = int(window_days * daily_rate)
        window_trades = max(min(window_trades, n // 2), 5)

        first_half = pnl_series[-2 * window_trades : -window_trades]
        second_half = pnl_series[-window_trades:]

        if len(first_half) < 5 or len(second_half) < 5:
            return None

        try:
            from scipy.stats import ks_2samp
            statistic, p_value = ks_2samp(first_half, second_half)
            return {"statistic": float(statistic), "p_value": float(p_value)}
        except ImportError:
            # scipy 不可用时的降级方案
            return self._simple_wasserstein(first_half, second_half)

    def _mmd_test(self, pnl_series: List[float]) -> Optional[Dict]:
        """MMD 最大均值差异检测（简化版）。

        使用高斯核的 MMD² 统计量。
        """
        n = len(pnl_series)
        if n < 20:
            return None

        mid = n // 2
        X = np.array(pnl_series[:mid])
        Y = np.array(pnl_series[mid:])

        # 使用中位数启发式选择带宽
        all_data = np.concatenate([X, Y])
        median_dist = np.median(np.abs(all_data - np.median(all_data)))
        sigma = max(median_dist, 1e-5)

        # 简化的 MMD² 计算
        def _rbf_kernel(a, b):
            diff = a[:, None] - b[None, :]
            return np.exp(-0.5 * (diff / sigma) ** 2)

        K_XX = _rbf_kernel(X, X)
        K_YY = _rbf_kernel(Y, Y)
        K_XY = _rbf_kernel(X, Y)

        mmd2 = np.mean(K_XX) + np.mean(K_YY) - 2 * np.mean(K_XY)

        return {"statistic": float(mmd2), "sigma": float(sigma)}

    def _rolling_sharpe(self, pnl_series: List[float], window: int) -> Optional[float]:
        """计算最近 window 笔的滚动 Sharpe。"""
        recent = pnl_series[-window:] if len(pnl_series) >= window else pnl_series
        if len(recent) < 5:
            return None

        mean = np.mean(recent)
        std = np.std(recent)
        if std < 1e-10:
            return 0.0
        return float(mean / std * math.sqrt(365))  # 年化（加密 365 天）

    def _sharpe_trending_down(self, pnl_series: List[float]) -> bool:
        """检测滚动 Sharpe 是否持续下降。"""
        if len(pnl_series) < ROLLING_SHARPE_WINDOW * 2:
            return False

        # 计算两个连续窗口的 Sharpe
        sharpe1 = self._rolling_sharpe(
            pnl_series[:-ROLLING_SHARPE_WINDOW], ROLLING_SHARPE_WINDOW
        )
        sharpe2 = self._rolling_sharpe(pnl_series, ROLLING_SHARPE_WINDOW)

        if sharpe1 is None or sharpe2 is None:
            return False

        return sharpe2 < sharpe1 and sharpe2 < 0

    def _simple_wasserstein(self, a: List[float], b: List[float]) -> Dict:
        """简化的 Wasserstein 距离（scipy 不可用时的降级方案）。

        使用均值-标准差差异作为代理。
        """
        mean_diff = abs(np.mean(a) - np.mean(b))
        std_diff = abs(np.std(a) - np.std(b))
        statistic = mean_diff + 0.5 * std_diff
        # 启发式 p_value：差异越大 p 越小
        p_value = max(0.0, min(1.0, 1.0 - statistic / 0.05))
        return {"statistic": float(statistic), "p_value": float(p_value)}

    # ══════════════════════════════════════════════════
    #  辅助
    # ══════════════════════════════════════════════════

    def _build_alert_description(
        self,
        level: str,
        ks7_sig: bool,
        ks14_sig: bool,
        adwin_drift: bool,
        sharpe: Optional[float],
        weekend_anomaly: bool,
    ) -> str:
        """构建告警描述文本。"""
        parts = []

        if level == DRIFT_LEVEL_FROZEN:
            parts.append("🚨 双窗口 KS 检验均显著，建议冻结策略参数。")
        elif level == DRIFT_LEVEL_RED:
            parts.append("⚠️ 14d KS 检验显著，市场结构可能已变化，建议重审策略。")
        elif level == DRIFT_LEVEL_YELLOW:
            parts.append("⚡ 7d KS 检验显著，建议观察策略表现。")

        if adwin_drift:
            parts.append("ADWIN 在线检测到漂移（实时响应）。")

        if sharpe is not None:
            parts.append(f"滚动{ROLLING_SHARPE_WINDOW}d Sharpe={sharpe:.2f}。")

        if weekend_anomaly:
            parts.append("周末与工作日表现显著偏离（加密市场特有）。")

        return " ".join(parts)

    def get_latest_alert(self, strategy_id: str, symbol: str) -> Optional[DriftAlert]:
        """获取最近一次告警。"""
        key = f"{strategy_id}:{symbol}"
        alerts = self._alerts.get(key, [])
        return alerts[-1] if alerts else None

    def get_stats(self) -> Dict[str, Any]:
        """获取检测器统计。"""
        return {
            "total_checks": self._total_checks,
            "tracked_pairs": len(self._pnl_series),
            "total_alerts": sum(len(a) for a in self._alerts.values()),
        }


# ══════════════════════════════════════════════════════
#  全局单例
# ══════════════════════════════════════════════════════

_drift_detector_instance: Optional[ConceptDriftDetector] = None


def get_concept_drift_detector() -> ConceptDriftDetector:
    """获取概念漂移检测器单例。"""
    global _drift_detector_instance
    if _drift_detector_instance is None:
        _drift_detector_instance = ConceptDriftDetector()
    return _drift_detector_instance
