"""FactorEvaluationPipeline — 因子评估闭环（2026-06-18 因子系统升级）。

衔接 compute_all_factors 和 generate_signals，接入三个之前写了没用的组件：
1. FactorEvaluator（IC/ICIR 评估）
2. FactorDecayMonitor（衰变惩罚）
3. DynamicFactorWeighting（regime 自适应权重）

实时模式：无前瞻收益，只做 regime 权重 + 衰变惩罚
回测模式：有前瞻收益，完整 IC 评估 + 权重学习
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class FactorEvaluationPipeline:
    """因子评估流水线 — 单例。"""

    _instance: Optional["FactorEvaluationPipeline"] = None

    def __init__(self):
        self._weighting = None
        self._decay_monitor = None
        # 整改#4：学习型因子加权（默认 mode='regime' → 与旧行为完全一致，仅在 learned/hybrid 时启用）
        self._learned = None
        self._weighting_mode = os.getenv("FACTOR_WEIGHTING_MODE", "regime").strip().lower()
        self._init_components()

    def _init_components(self):
        """懒加载评估组件（容错：某个组件不可用不影响其他）。"""
        # DynamicFactorWeighting（regime 权重）
        # [2026-07-17 修复] 此前 `DynamicFactorWeighting()` 少传必需的 `factor_engine`
        # 位置参数，构造直接 TypeError，被下面的 except 静默吞掉（仅 debug 级日志），
        # 导致 regime 自适应权重自系统上线起从未真正生效过一次——不管市场是趋势还是
        # 震荡，一直在用近似等权的因子集合，这正是"震荡行情下动量类因子照样重仓、
        # 因子方向与实际盈亏反相关"的根因之一。改用 warning 级日志，避免同类问题再被吞掉。
        try:
            from backend.services.factor_engine.factor_weighting import DynamicFactorWeighting
            from backend.services.factor_engine.base_factors import factor_engine as _shared_factor_engine
            self._weighting = DynamicFactorWeighting(_shared_factor_engine)
        except Exception as e:
            logger.warning(f"[FactorPipeline] DynamicFactorWeighting 加载失败，regime 自适应权重将不生效: {e}")

        # FactorDecayMonitor（衰变惩罚）
        try:
            from backend.services.factor_engine.factor_decay_monitor import decay_monitor
            self._decay_monitor = decay_monitor
        except Exception as e:
            logger.debug(f"[FactorPipeline] FactorDecayMonitor 加载失败: {e}")

        # 整改#4：仅在非 regime 模式下加载学习层（zero-risk：默认 regime 不触碰）
        if self._weighting_mode in ("learned", "hybrid"):
            try:
                from backend.services.factor_engine.learned_weighting import (
                    LearnedFactorWeighting,
                    LearnedWeightingConfig,
                )
                self._learned = LearnedFactorWeighting(LearnedWeightingConfig())
                logger.info("[FactorPipeline] 学习型因子加权已启用 mode=%s", self._weighting_mode)
            except Exception as e:  # noqa: BLE001
                logger.warning("[FactorPipeline] 学习层加载失败，回退 regime: %s", e)
                self._weighting_mode = "regime"

    def compute_weighted_signals(
        self,
        factor_values: Dict[str, Any],
        market_data: Optional[Dict] = None,
    ):
        """计算加权因子信号（替代 generate_signals 的等权调用）。

        Args:
            factor_values: compute_all_factors 的输出（Dict[str, FactorValue]）
            market_data: 市场数据（含 regime 信息）

        Returns:
            CompositeSignal（和 FactorSignalGenerator.generate_signals 输出格式一致）
        """
        from backend.services.factor_engine.factor_signal_generator import FactorSignalGenerator

        if not factor_values:
            return None

        # 1. 计算权重
        weights = self._compute_weights(factor_values, market_data)

        # 2. 用权重生成信号
        generator = FactorSignalGenerator()
        composite = generator.generate_signals(factor_values, weights=weights)

        # 3. 整改#4：hybrid / learned 模式融合学习层分数
        composite = self._blend_learned_signal(factor_values, market_data, composite)

        return composite

    def _blend_learned_signal(
        self,
        factor_values: Dict[str, Any],
        market_data: Optional[Dict],
        composite,
    ):
        """hybrid：regime 与 learned 按置信度融合；learned：learned 主路径。"""
        if self._weighting_mode not in ("learned", "hybrid"):
            return composite
        if composite is None:
            return composite
        try:
            learned = self._resolve_learned()
            if learned is None or learned.model is None or not learned.feature_columns:
                if self._weighting_mode == "learned":
                    # learned 模式未训练时降级为弱信号
                    composite.confidence *= 0.5
                return composite

            row = {}
            for name, fv in factor_values.items():
                row[name] = float(getattr(fv, "normalized", getattr(fv, "value", 0.0)) or 0.0)
            feat_df = pd.DataFrame([row])
            score = float(learned.predict_score(feat_df).iloc[-1])
            learned_dir = max(-1.0, min(1.0, score * 5.0))
            learned_strength = max(0.0, min(1.0, abs(score) * 3.0))

            if self._weighting_mode == "learned":
                composite.direction = learned_dir
                composite.strength = max(composite.strength, learned_strength)
                composite.confidence = max(composite.confidence, min(1.0, abs(learned_dir)))
                return composite

            # hybrid：晋升门阶段权重（shadow=0 / canary=0.22 / full=0.45）
            try:
                from backend.services.promotion_scan_service import get_effective_learned_blend
                alpha = get_effective_learned_blend("ml_learned_weighting")
            except Exception:
                alpha = float(os.environ.get("HYBRID_LEARNED_BLEND", "0.45"))
            alpha = max(0.0, min(1.0, alpha))
            if alpha <= 0:
                return composite
            composite.direction = (1 - alpha) * composite.direction + alpha * learned_dir
            composite.strength = max(composite.strength, (1 - alpha) * composite.strength + alpha * learned_strength)
            composite.confidence = max(composite.confidence, min(1.0, 0.5 + 0.5 * abs(learned_dir)))
            return composite
        except Exception as exc:
            logger.debug("[FactorPipeline] learned 融合跳过: %s", exc)
            return composite

    def _resolve_learned(self):
        """优先用 ML 激活服务训练好的单例，否则用本地懒加载实例。"""
        if self._learned is not None and getattr(self._learned, "model", None) is not None:
            return self._learned
        try:
            from backend.services.ml.activation_service import get_learned_weighting_singleton

            ext = get_learned_weighting_singleton()
            if ext is not None and getattr(ext, "model", None) is not None:
                self._learned = ext
                return ext
        except Exception:
            pass
        return self._learned

    def _compute_weights(
        self,
        factor_values: Dict[str, Any],
        market_data: Optional[Dict],
    ) -> Dict[str, float]:
        """计算每个因子的最终权重 = regime 基础权重 × 衰变惩罚。"""
        factor_names = list(factor_values.keys())

        # 基础权重：默认等权 1.0
        # [fix] 无数据因子（has_data=False）或无方向语义因子（is_directional=False，
        # 如价格/成交量绝对值类）权重设 0，让 _aggregate 的 `if w <= 0: continue` 自动跳过。
        # is_directional 过滤根治策略偏多：价格/量类因子 value 永远正，被当看多拉偏 direction。
        base_weights = {}
        for name in factor_names:
            fv = factor_values.get(name)
            if fv is not None and (
                getattr(fv, "has_data", True) is False
                or getattr(fv, "is_directional", True) is False
            ):
                base_weights[name] = 0.0
            else:
                base_weights[name] = 1.0

        # 叠加 regime 自适应权重（如果 DynamicFactorWeighting 可用）
        # [2026-07-17 修复] 两处问题一并修掉：
        # 1) 实例方法是 `calculate_adaptive_weights`，`get_adaptive_weights` 只是模块级
        #    便捷函数——旧代码调实例方法时方法不存在，AttributeError 被吞掉。
        # 2) 返回值是 `AdaptiveWeights` 数据类而非 dict，`isinstance(..., dict)` 恒为
        #    False，整段代码等于从没执行过。
        # 3) REGIME_WEIGHTS 每个 regime 只挑 ~12 个因子瓜分总量=1.0 的相对配额（如
        #    NOISE 下 atr=0.15），量级比其余因子的等权基准 1.0 小一个数量级；如果直接
        #    `base_weights[name] = regime_weights[name]` 覆盖，被选中强调的因子反而会
        #    比"没被提及、保持1.0"的因子更轻，方向正好和设计意图相反。这里改成按均值
        #    归一化成"围绕1.0的乘数"后再乘进 base_weights，不覆盖。
        if self._weighting is not None:
            try:
                adaptive = self._weighting.calculate_adaptive_weights(factor_values, market_data)
                regime_weights = getattr(adaptive, "weights", None) if adaptive is not None else None
                if regime_weights:
                    positive = [v for v in regime_weights.values() if v and v > 0]
                    if positive:
                        mean_rw = sum(positive) / len(positive)
                        if mean_rw > 0:
                            for name in factor_names:
                                rw = regime_weights.get(name)
                                if rw and rw > 0:
                                    base_weights[name] *= (rw / mean_rw)
            except Exception as e:
                logger.warning(f"[FactorPipeline] regime 权重计算跳过: {e}")

        # 叠加衰变惩罚（如果 FactorDecayMonitor 可用）
        if self._decay_monitor is not None:
            for name in factor_names:
                try:
                    penalty = self._decay_monitor.get_factor_weight_penalty(name)
                    if penalty is not None and penalty < 1.0:
                        base_weights[name] *= penalty
                except Exception:
                    pass  # 衰变查询失败不影响整体

        # 叠加 IC 闭环运行时权重（factor_ic_evaluator 产出，胜率高升权/连亏降权）
        try:
            from backend.services.factor_ic_evaluator import load_runtime_factor_weights
            runtime_weights = load_runtime_factor_weights()
            if runtime_weights:
                for name in factor_names:
                    rw = runtime_weights.get(name)
                    if rw is not None:
                        base_weights[name] *= float(rw)
        except Exception as e:
            logger.debug(f"[FactorPipeline] IC 运行时权重跳过: {e}")

        # [2026-08-14 P1-C4] PAPER 影子因子权重上限（拍板：PAPER 可交易但权重受限）。
        # factor_active_set.state=PAPER 的因子在线权重强制 min(w, cap)，避免未经
        # SMALL_LIVE/ACTIVE 审批的影子因子占据过大合成份额。
        try:
            from backend.config import settings as _s_cfg
            _paper_cap = float(getattr(_s_cfg, "PAPER_FACTOR_WEIGHT_CAP", 0.5) or 0.5)
            if _paper_cap > 0:
                from backend.services.scalp.scalp_factor_exclude import get_paper_factor_ids
                from backend.services.factor_engine.key_utils import normalize_engine_key
                _paper_ids = get_paper_factor_ids()
                if _paper_ids:
                    for name in factor_names:
                        if normalize_engine_key(name) in _paper_ids:
                            base_weights[name] = min(base_weights[name], _paper_cap)
        except Exception as e:
            logger.debug(f"[FactorPipeline] PAPER 权重上限跳过: {e}")

        # 归一化（防止某些因子权重过大）
        total = sum(base_weights.values())
        if total > 0:
            base_weights = {k: v / total * len(factor_names) for k, v in base_weights.items()}

        return base_weights

    def record_evaluation(
        self,
        factor_name: str,
        factor_values_series,
        forward_returns,
    ):
        """回测后调用：记录因子 IC 到衰变监控。

        Args:
            factor_name: 因子名
            factor_values_series: 因子值序列（pd.Series）
            forward_returns: 前瞻收益序列（pd.Series）
        """
        if self._decay_monitor is None:
            return
        try:
            from backend.services.factor_engine.factor_evaluator import FactorEvaluator
            evaluator = FactorEvaluator()
            metrics = evaluator.evaluate_factor(factor_name, factor_values_series, forward_returns)
            if metrics and hasattr(metrics, 'ic_mean'):
                self._decay_monitor.record_ic(factor_name, metrics.ic_mean)
        except Exception as e:
            logger.debug(f"[FactorPipeline] IC 记录跳过 {factor_name}: {e}")


# 全局单例
factor_pipeline = FactorEvaluationPipeline()
