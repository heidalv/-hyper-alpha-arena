"""
FactorQualityEvaluator — 因子数据质量评估框架

评估因子数据的完整性、一致性和新鲜度，
为 DecisionFusionEngine 提供质量报告以调整决策置信度。
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .base_factors import FactorValue

logger = logging.getLogger(__name__)


@dataclass
class QualityReport:
    """因子数据质量报告"""
    data_completeness: float   # [0, 1] 有多少因子成功计算
    signal_agreement: float    # [0, 1] 因子方向一致性
    outlier_count: int         # 异常值因子数量
    missing_factors: List[str] = field(default_factory=list)
    stale_factors: List[str] = field(default_factory=list)
    overall_quality: str = "low"  # "high" / "medium" / "low"


class FactorQualityEvaluator:
    """评估因子数据质量"""

    # 因子值超过此阈值视为异常值
    OUTLIER_Z_THRESHOLD: float = 3.0

    # 因子值变化低于此阈值视为陈旧（可能数据断流）
    STALE_DELTA_THRESHOLD: float = 1e-8

    def evaluate(
        self,
        factor_values: Dict[str, FactorValue],
        expected_factors: List[str],
        previous_values: Optional[Dict[str, float]] = None,
    ) -> QualityReport:
        """
        评估因子数据质量。

        Args:
            factor_values: 因子名 -> FactorValue（本次计算结果）
            expected_factors: 期望计算的因子名列表
            previous_values: 上一轮因子值（用于检测陈旧数据）

        Returns:
            QualityReport
        """
        # 1. 数据完整性
        missing = [f for f in expected_factors if f not in factor_values]
        # [2026-08-14 P2-7 修复] completeness 应为「期望因子中命中的比例」。
        # 旧算法 len(factor_values)/len(expected) 统计的是"返回了多少因子"：
        # expected=[A,B,C]、实际={A,X,Y}（B/C 缺失）时 completeness=1.0 虚高；
        # 返回多于期望时甚至 >1。修正为命中期望因子数的比例。
        hit = len([f for f in expected_factors if f in factor_values])
        completeness = hit / max(len(expected_factors), 1)

        # 2. 异常值检测
        outlier_count = self._count_outliers(factor_values)

        # 3. 陈旧因子检测
        stale = self._detect_stale(factor_values, previous_values)

        # 4. 信号方向一致性
        agreement = self._compute_agreement(factor_values)

        # 5. 质量等级判定
        quality = self._classify_quality(completeness, agreement)

        return QualityReport(
            data_completeness=completeness,
            signal_agreement=agreement,
            outlier_count=outlier_count,
            missing_factors=missing,
            stale_factors=stale,
            overall_quality=quality,
        )

    def _count_outliers(self, factor_values: Dict[str, FactorValue]) -> int:
        """统计异常值因子数量"""
        if not factor_values:
            return 0

        values = [fv.value for fv in factor_values.values()
                  if fv.value is not None and _is_finite(fv.value)]
        if len(values) < 3:
            return 0

        mean_v = sum(values) / len(values)
        std_v = math.sqrt(sum((v - mean_v) ** 2 for v in values) / len(values))
        if std_v < 1e-10:
            return 0

        count = 0
        for v in values:
            z = abs(v - mean_v) / std_v
            if z > self.OUTLIER_Z_THRESHOLD:
                count += 1
        return count

    def _detect_stale(
        self,
        factor_values: Dict[str, FactorValue],
        previous_values: Optional[Dict[str, float]],
    ) -> List[str]:
        """检测陈旧因子（值未变化，可能数据断流）"""
        if previous_values is None:
            return []

        stale = []
        for name, fv in factor_values.items():
            prev = previous_values.get(name)
            if prev is not None and fv.value is not None:
                if abs(fv.value - prev) < self.STALE_DELTA_THRESHOLD:
                    stale.append(name)
        return stale

    def _compute_agreement(self, factor_values: Dict[str, FactorValue]) -> float:
        """
        计算因子方向一致性。

        [2026-08-14 P2-7 修复] 旧实现直接取 fv.value 的原始符号：对 RSI(>50 看空)、
        funding_rate(正=过热看空) 等反向因子，原始值符号与实际信号方向**相反**，
        且不同量纲的因子混求 sign 无意义。修复：复用 FactorSignalGenerator 的
        方向映射后再取符号一致性（与合成 direction 同口径）。

        一致性 = |sum(signs)| / count（全部同方向=1，均分=0）。
        """
        if not factor_values:
            return 0.0

        try:
            from .factor_signal_generator import FactorSignalGenerator
            _mapper = FactorSignalGenerator()._map_direction
        except Exception:
            _mapper = None

        signs = []
        for name, fv in factor_values.items():
            if fv.value is None or not _is_finite(fv.value):
                continue
            if _mapper is not None:
                direction = _mapper(name, fv.value)
                signs.append(1 if direction > 0.05 else (-1 if direction < -0.05 else 0))
            else:
                signs.append(1 if fv.value > 0 else (-1 if fv.value < 0 else 0))

        if not signs:
            return 0.0

        abs_sum = abs(sum(signs))
        return abs_sum / len(signs)

    def _classify_quality(self, completeness: float, agreement: float) -> str:
        """判定质量等级"""
        if completeness >= 0.8 and agreement >= 0.6:
            return "high"
        elif completeness >= 0.6 and agreement >= 0.4:
            return "medium"
        return "low"


def _is_finite(value: float) -> bool:
    """检查浮点值是否有限"""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
