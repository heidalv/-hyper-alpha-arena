"""
Walk-Forward 验证器 — P2.7
滚动窗口 KS 检验 + 滚动 Sharpe 监控 + 过拟合检测。

加密适配：
- 使用 30d 滚动窗口（替代传统 90d）
- 适应加密市场更短的周期特性
- 识别牛市/熊市/震荡三种区制下的性能漂移
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class WalkForwardValidator:
    """
    Walk-Forward 验证器

    验证流程：
    1. OOS (Out-of-Sample) 滚动窗口回测
    2. IS/OOS 分布 KS 检验（检测过拟合）
    3. 滚动 Sharpe 监控（检测性能衰减）
    4. 区制（regime）性能对比
    """

    _instance: Optional["WalkForwardValidator"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._validation_history: List[Dict[str, Any]] = []
        logger.info("[WalkForward] Walk-Forward 验证器初始化完成")

    @classmethod
    def get_instance(cls) -> "WalkForwardValidator":
        return cls()

    def _is_enabled(self) -> bool:
        try:
            from backend.config.settings import AI_WALK_FORWARD_VALIDATION_ENABLED
            return bool(AI_WALK_FORWARD_VALIDATION_ENABLED)
        except Exception:
            return False

    def validate(
        self,
        returns_is: List[float],
        returns_oos: List[float],
        *,
        is_label: str = "IS",
        oos_label: str = "OOS",
        min_samples: int = 20,
        periods_per_year: float = 8760.0,  # [P1-9] 年化期数：默认 h1（24×365）
    ) -> Dict[str, Any]:
        """
        执行 Walk-Forward 验证。

        Args:
            returns_is: 样本内收益序列
            returns_oos: 样本外收益序列
            is_label: IS标签
            oos_label: OOS标签
            min_samples: 最小样本数

        Returns:
            {
                "passed": bool,              # 是否通过
                "ks_statistic": float,       # KS统计量
                "ks_pvalue": float,          # KS p值
                "sharpe_is": float,
                "sharpe_oos": float,
                "sharpe_degradation": float, # Sharpe衰减率
                "overfit_score": float,      # 过拟合分数(0-1, >0.5警告)
                "regime_breakdown": {...},   # 分区制性能
                "recommendation": str,
            }
        """
        if not self._is_enabled():
            return {"skipped": "AI_WALK_FORWARD_VALIDATION_ENABLED=false"}

        is_arr = np.array(returns_is, dtype=float)
        oos_arr = np.array(returns_oos, dtype=float)

        # 清洗
        is_arr = is_arr[np.isfinite(is_arr)]
        oos_arr = oos_arr[np.isfinite(oos_arr)]

        if len(is_arr) < min_samples or len(oos_arr) < min_samples:
            return {
                "passed": False,
                "reason": f"样本不足: IS={len(is_arr)}, OOS={len(oos_arr)} < {min_samples}",
            }

        result: Dict[str, Any] = {}

        # 1. KS检验
        from scipy import stats
        ks_stat, ks_pvalue = stats.ks_2samp(is_arr, oos_arr)
        result["ks_statistic"] = round(float(ks_stat), 4)
        result["ks_pvalue"] = round(float(ks_pvalue), 4)

        # 2. Sharpe计算（[P1-9] 按收益频率年化，默认 h1=8760）
        sharpe_is = self._calc_sharpe(is_arr, periods_per_year=periods_per_year)
        sharpe_oos = self._calc_sharpe(oos_arr, periods_per_year=periods_per_year)
        result["sharpe_is"] = round(sharpe_is, 4)
        result["sharpe_oos"] = round(sharpe_oos, 4)

        # Sharpe衰减率
        if sharpe_is > 0.01:
            degradation = (sharpe_is - sharpe_oos) / sharpe_is
            result["sharpe_degradation"] = round(degradation, 4)
        else:
            result["sharpe_degradation"] = 0.0

        # 3. 过拟合分数
        overfit = self._calc_overfit_score(
            ks_pvalue=ks_pvalue,
            sharpe_degradation=result.get("sharpe_degradation", 0),
            is_sharpe=sharpe_is,
            oos_sharpe=sharpe_oos,
        )
        result["overfit_score"] = round(overfit, 4)

        # 4. 滚动Sharpe监控
        roll_sharpe = self._rolling_sharpe(oos_arr, window=len(oos_arr) // 3 or 10)
        if roll_sharpe:
            result["rolling_sharpe_min"] = round(min(roll_sharpe), 4)
            result["rolling_sharpe_max"] = round(max(roll_sharpe), 4)
            result["rolling_sharpe_last"] = round(roll_sharpe[-1], 4)
            result["rolling_sharpe_trend"] = 1.0 if len(roll_sharpe) >= 2 and roll_sharpe[-1] >= roll_sharpe[-2] else -1.0

        # 5. 区制拆分（基于OOS数据）
        regime = self._regime_breakdown(oos_arr)
        result["regime_breakdown"] = regime

        # 6. 综合判定
        passed = (
            ks_pvalue > 0.05  # 分布无显著差异
            and sharpe_oos > 0
            and overfit < 0.6
        )

        result["passed"] = passed
        result["recommendation"] = self._make_recommendation(passed, result)

        # 记录历史
        self._validation_history.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "passed": passed,
            **{k: v for k, v in result.items() if k != "regime_breakdown"},
        })
        self._validation_history = self._validation_history[-50:]

        return result

    def validate_rolling(
        self,
        all_returns: List[float],
        *,
        is_ratio: float = 0.5,
        min_is_size: int = 20,
        step_size: int = 10,
    ) -> Dict[str, Any]:
        """
        滚动 Walk-Forward 验证。

        模拟真实时序：用前 is_ratio 的样本训练/评估，用后续样本验证，
        逐step滚动，汇总各窗口结果。

        Returns:
            {
                "windows": [{window_i: result}, ...],
                "pass_rate": float,
                "avg_sharpe_oos": float,
                "avg_overfit_score": float,
            }
        """
        all_arr = np.array(all_returns, dtype=float)
        all_arr = all_arr[np.isfinite(all_arr)]

        if len(all_arr) < min_is_size * 2:
            return {"passed": False, "reason": "总样本不足", "windows": []}

        is_size = max(min_is_size, int(len(all_arr) * is_ratio))
        windows = []
        total_steps = max(1, (len(all_arr) - is_size - min_is_size) // step_size + 1)

        for i in range(total_steps):
            start = i * step_size
            is_data = all_arr[start : start + is_size]
            oos_data = all_arr[start + is_size : start + is_size + min_is_size]

            if len(is_data) < min_is_size or len(oos_data) < min_is_size:
                break

            wf = self.validate(list(is_data), list(oos_data))
            wf["window"] = i
            wf["is_start"] = start
            wf["oos_start"] = start + is_size
            windows.append(wf)

        if not windows:
            return {"passed": False, "reason": "无法生成滚动窗口", "windows": []}

        pass_rate = sum(1 for w in windows if w.get("passed")) / len(windows)
        avg_sharpe = float(np.mean([w.get("sharpe_oos", 0) for w in windows]))
        avg_overfit = float(np.mean([w.get("overfit_score", 0) for w in windows]))

        return {
            "passed": pass_rate > 0.5,
            "pass_rate": round(pass_rate, 4),
            "avg_sharpe_oos": round(avg_sharpe, 4),
            "avg_overfit_score": round(avg_overfit, 4),
            "windows": windows,
        }

    # ── 内部方法 ──

    @staticmethod
    def _calc_sharpe(returns: np.ndarray, periods_per_year: float = 8760.0) -> float:
        """计算年化Sharpe。

        [P1-9] 年化期数参数化：原硬编码 sqrt(8760)（h1 假设），传入 4h/1d/交易级
        收益时年化差 sqrt(6)/sqrt(365) 倍。默认仍为 8760 保持兼容。
        """
        if len(returns) < 2:
            return 0.0
        mu = float(np.mean(returns))
        sigma = float(np.std(returns))
        if sigma < 1e-10:
            return 0.0
        _ppy = float(periods_per_year or 8760.0)
        if _ppy <= 0:
            _ppy = 8760.0
        return mu / sigma * math.sqrt(_ppy)

    @staticmethod
    def _calc_overfit_score(
        ks_pvalue: float,
        sharpe_degradation: float,
        is_sharpe: float,
        oos_sharpe: float,
    ) -> float:
        """
        过拟合综合评分（0-1，越高越可能过拟合）。

        考虑：
        - KS p值（越小→分布变化越大→过拟合）
        - Sharpe衰减率（Sharpe从IS到OOS下降比例）
        - IS/OOS Sharpe比值
        """
        score = 0.0

        # KS p值分量
        if ks_pvalue < 0.01:
            score += 0.4
        elif ks_pvalue < 0.05:
            score += 0.25
        elif ks_pvalue < 0.10:
            score += 0.1

        # Sharpe衰减分量
        degradation = max(0, sharpe_degradation)
        if degradation > 0.5:
            score += 0.4
        elif degradation > 0.3:
            score += 0.25
        elif degradation > 0.1:
            score += 0.1

        # IS/OOS比值分量
        if is_sharpe > 0.1 and oos_sharpe > 0:
            ratio = is_sharpe / oos_sharpe
            if ratio > 3:
                score += 0.2
            elif ratio > 2:
                score += 0.1

        return min(score, 1.0)

    @staticmethod
    def _rolling_sharpe(returns: np.ndarray, window: int = 10,
                        periods_per_year: float = 8760.0) -> List[float]:
        """计算滚动Sharpe序列（[P1-9] 年化期数参数化）"""
        if len(returns) < window:
            return []
        _ppy = float(periods_per_year or 8760.0)
        results = []
        for i in range(window, len(returns) + 1):
            w = returns[i - window : i]
            mu = float(np.mean(w))
            sigma = float(np.std(w))
            if sigma < 1e-10:
                results.append(0.0)
            else:
                results.append(mu / sigma * math.sqrt(_ppy))
        return results

    @staticmethod
    def _regime_breakdown(returns: np.ndarray) -> Dict[str, Any]:
        """
        基于收益序列自动划分牛市/熊市/震荡三种区制。
        加密适配：使用更窄的区间定义。
        """
        if len(returns) < 10:
            return {}
        mu = float(np.mean(returns))
        sigma = float(np.std(returns))

        bull: List[float] = []
        bear: List[float] = []
        sideways: List[float] = []

        for r in returns:
            if r > mu + 0.5 * sigma:
                bull.append(r)
            elif r < mu - 0.5 * sigma:
                bear.append(r)
            else:
                sideways.append(r)

        def regime_stats(r_list):
            if not r_list:
                return {
                    "count": 0,
                    "pct": 0,
                    "mean_return": 0,
                    "sharpe": 0,
                }
            arr = np.array(r_list)
            return {
                "count": len(arr),
                "pct": round(len(arr) / len(returns), 4),
                "mean_return": round(float(np.mean(arr)), 6),
                "sharpe": round(
                    float(np.mean(arr)) / max(float(np.std(arr)), 1e-10) * math.sqrt(8760),
                    4,
                ),
            }

        return {
            "bull": regime_stats(bull),
            "bear": regime_stats(bear),
            "sideways": regime_stats(sideways),
        }

    @staticmethod
    def _make_recommendation(passed: bool, result: Dict[str, Any]) -> str:
        """生成验证建议"""
        if passed:
            overfit = result.get("overfit_score", 0)
            if overfit < 0.2:
                return "✅ 通过：策略在OOS上表现稳健，过拟合风险低，建议晋升。"
            return "⚠️ 通过但有风险：考虑增加OOS验证周期或降低参数自由度。"

        sharpe_oos = result.get("sharpe_oos", 0)
        overfit = result.get("overfit_score", 0)

        if sharpe_oos <= 0:
            return "❌ 未通过：OOS Sharpe≤0，策略在样本外无盈利能力。"
        if overfit > 0.6:
            return "❌ 未通过：过拟合严重（overfit>0.6），建议简化参数或增加正则化。"
        return "❌ 未通过：分布漂移显著，策略不具备泛化能力。"

    # ── 与 EvolutionScheduler 集成 ──

    def validate_strategy(
        self,
        db,
        strategy_id: str,
        returns: List[float],
    ) -> Dict[str, Any]:
        """便捷接口：对策略做完整WF验证"""
        result = self.validate_rolling(returns)
        result["strategy_id"] = strategy_id
        result["validated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            from backend.database.models import StrategyMemory
            memory = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()
            if memory:
                key_lessons = memory.key_lessons or []
                key_lessons.append({
                    "type": "walk_forward",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "passed": result.get("passed", False),
                    "pass_rate": result.get("pass_rate", 0),
                    "avg_sharpe_oos": result.get("avg_sharpe_oos", 0),
                })
                memory.key_lessons = key_lessons[-50:]
                db.commit()
        except Exception as exc:
            logger.debug(f"[WalkForward] 策略记忆更新失败: {exc}")

        return result

    def get_status(self) -> Dict[str, Any]:
        return {
            "validations_total": len(self._validation_history),
            "recent_pass_rate": round(
                sum(1 for v in self._validation_history[-10:] if v.get("passed"))
                / max(1, len(self._validation_history[-10:])),
                4,
            ) if self._validation_history else 0,
        }


# 全局单例
walk_forward_validator = WalkForwardValidator.get_instance()
