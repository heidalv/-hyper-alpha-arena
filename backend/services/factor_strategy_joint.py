"""
因子-策略贝叶斯联合模型 (P0.4) — 轻量级 O(1) 每笔更新

核心职责：
1. 对每个 (因子, 市况, 策略) 组合维护 Beta 分布后验
2. 每笔交易完成后 O(1) 更新后验
3. 查询时返回 P(盈利 | 因子值, 市况, 策略) 及其置信度
4. 加密适配：资金费率/OI/爆仓为一等因子

设计原理：
- 使用 discretized bins 将连续因子值映射到离散区间
- 每个 bin 维护 Beta(α=赢+1, β=输+1) 后验
- 更新: α += is_win, β += is_loss  (O(1))
- 查询: mean = α/(α+β), confidence = 1/(α+β)  (O(1))
"""

import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 加密一等因子的离散化配置 ──
FACTOR_BIN_CONFIG = {
    "funding_rate": {
        "bins": [-0.001, -0.0005, -0.0001, 0.0001, 0.0005, 0.001],
        "labels": ["extreme_neg", "high_neg", "slight_neg", "neutral",
                    "slight_pos", "high_pos", "extreme_pos"],
        "is_crypto_primary": True,
        "weight": 0.25,
    },
    "oi_change_pct": {
        "bins": [-0.15, -0.05, 0.0, 0.05, 0.15],
        "labels": ["sharp_decline", "moderate_decline", "flat", "moderate_rise", "sharp_rise"],
        "is_crypto_primary": True,
        "weight": 0.20,
    },
    "liquidation_imbalance": {
        "bins": [-0.6, -0.3, 0.0, 0.3, 0.6],
        "labels": ["longs_liquidated", "slight_long_liquidation", "balanced",
                    "slight_short_liquidation", "shorts_liquidated"],
        "is_crypto_primary": True,
        "weight": 0.18,
    },
    "stablecoin_flow": {
        "bins": [-0.10, -0.03, 0.0, 0.03, 0.10],
        "labels": ["strong_outflow", "slight_outflow", "flat", "slight_inflow", "strong_inflow"],
        "is_crypto_primary": True,
        "weight": 0.15,
    },
    "rsi_14": {
        "bins": [30, 40, 50, 60, 70],
        "labels": ["oversold", "weak", "neutral", "strong", "overbought"],
        "is_crypto_primary": False,
        "weight": 0.08,
    },
    "volume_ratio": {
        "bins": [0.5, 0.8, 1.0, 1.5, 2.0],
        "labels": ["very_low", "low", "normal", "high", "very_high"],
        "is_crypto_primary": False,
        "weight": 0.06,
    },
    "volatility_30d": {
        "bins": [0.3, 0.5, 0.8, 1.2],
        "labels": ["low_vol", "moderate_vol", "high_vol", "extreme_vol"],
        "is_crypto_primary": False,
        "weight": 0.05,
    },
    "ema_trend_slope": {
        "bins": [-0.02, -0.005, 0.0, 0.005, 0.02],
        "labels": ["strong_bearish", "bearish", "flat", "bullish", "strong_bullish"],
        "is_crypto_primary": False,
        "weight": 0.03,
    },
}

# ── 市况离散化 ──
REGIME_BINS = ["bull_market", "bear_market", "ranging", "high_volatility", "event_driven"]

# Beta 先验参数（无信息先验）
DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 1.0


class FactorStrategyJointModel:
    """因子-策略贝叶斯联合模型（单例）。

    用法：
        model = get_factor_strategy_joint()
        model.update(strategy_id, symbol, factor_values, is_win)
        prob, confidence = model.query(strategy_id, symbol, factor_name, factor_value)
    """

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

        # 核心数据结构: (strategy_id, symbol, regime, factor_name, bin) → (alpha, beta)
        self._posteriors: Dict[Tuple, Tuple[float, float]] = {}
        self._data_lock = threading.Lock()

        # 缓存策略因子权重
        self._strategy_factor_weights: Dict[str, Dict[str, float]] = defaultdict(dict)

        # 统计信息
        self._total_updates = 0
        self._total_queries = 0

        logger.info("[FactorJoint] 因子-策略贝叶斯联合模型初始化完成 "
                     f"(factors={len(FACTOR_BIN_CONFIG)}, regimes={len(REGIME_BINS)})")

    # ══════════════════════════════════════════════════
    #  更新接口 (O(1))
    # ══════════════════════════════════════════════════

    def update(
        self,
        strategy_id: str,
        symbol: str,
        factor_values: Dict[str, float],
        is_win: bool,
        *,
        regime: str = "ranging",
    ) -> None:
        """每笔交易完成后调用，更新所有因子的 Beta 后验。

        Args:
            factor_values: {factor_name: float_value} 入场时刻的因子快照
            is_win: 该笔交易是否盈利
            regime: 入场时刻的市况标签

        Complexity: O(num_factors) = O(21) ≈ O(1)
        """
        # 规范化市况
        regime_normalized = self._normalize_regime(regime)

        with self._data_lock:
            for factor_name, bin_config in FACTOR_BIN_CONFIG.items():
                value = factor_values.get(factor_name)
                if value is None:
                    continue

                # 离散化
                bin_label = self._discretize(value, bin_config["bins"], bin_config["labels"])
                if bin_label is None:
                    continue

                key = (strategy_id, symbol, regime_normalized, factor_name, bin_label)

                alpha, beta = self._posteriors.get(key, (DEFAULT_ALPHA, DEFAULT_BETA))
                if is_win:
                    alpha += 1.0
                else:
                    beta += 1.0
                self._posteriors[key] = (alpha, beta)

            self._total_updates += 1

        logger.debug(
            f"[FactorJoint] update {strategy_id}/{symbol}: "
            f"win={is_win} regime={regime_normalized} total_updates={self._total_updates}"
        )

    def batch_update(
        self,
        strategy_id: str,
        symbol: str,
        outcomes: List[Dict[str, Any]],
    ) -> None:
        """批量更新（用于回测/历史数据回放）。

        Args:
            outcomes: [{"factor_values": {...}, "is_win": bool, "regime": str}, ...]
        """
        for outcome in outcomes:
            self.update(
                strategy_id=strategy_id,
                symbol=symbol,
                factor_values=outcome.get("factor_values", {}),
                is_win=outcome.get("is_win", False),
                regime=outcome.get("regime", "ranging"),
            )

    # ══════════════════════════════════════════════════
    #  查询接口 (O(1))
    # ══════════════════════════════════════════════════

    def query(
        self,
        strategy_id: str,
        symbol: str,
        factor_name: str,
        factor_value: float,
        *,
        regime: str = "ranging",
    ) -> Tuple[float, float]:
        """查询 P(盈利 | 因子值, 市况, 策略)。

        Returns:
            (probability, confidence)
            - probability: 后验均值 α/(α+β)
            - confidence: 样本量置信度 1-1/(α+β)  (越多样本置信度越高)
        """
        regime_normalized = self._normalize_regime(regime)

        bin_config = FACTOR_BIN_CONFIG.get(factor_name)
        if not bin_config:
            return 0.5, 0.0

        bin_label = self._discretize(factor_value, bin_config["bins"], bin_config["labels"])
        if bin_label is None:
            return 0.5, 0.0

        with self._data_lock:
            self._total_queries += 1
            key = (strategy_id, symbol, regime_normalized, factor_name, bin_label)
            alpha, beta = self._posteriors.get(key, (DEFAULT_ALPHA, DEFAULT_BETA))

        total = alpha + beta
        probability = alpha / total
        confidence = 1.0 - 1.0 / total  # 样本越多置信度越高，最少为 0

        return round(probability, 4), round(confidence, 4)

    def query_all_factors(
        self,
        strategy_id: str,
        symbol: str,
        factor_values: Dict[str, float],
        *,
        regime: str = "ranging",
    ) -> Dict[str, Dict[str, float]]:
        """一次查询所有因子的后验概率。

        Returns:
            {
                "factor_name": {
                    "probability": 0.65,   # P(盈利|因子)
                    "confidence": 0.80,    # 样本量置信度
                    "weight": 0.25,        # 因子权重（加密一等因子更高）
                    "sample_count": 12,    # 该bin的样本量
                    "bin": "high_pos",     # 离散化标签
                }
            }
        """
        regime_normalized = self._normalize_regime(regime)
        results = {}

        for factor_name, bin_config in FACTOR_BIN_CONFIG.items():
            value = factor_values.get(factor_name)
            if value is None:
                continue

            bin_label = self._discretize(value, bin_config["bins"], bin_config["labels"])
            if bin_label is None:
                continue

            with self._data_lock:
                key = (strategy_id, symbol, regime_normalized, factor_name, bin_label)
                alpha, beta = self._posteriors.get(key, (DEFAULT_ALPHA, DEFAULT_BETA))

            total = alpha + beta
            results[factor_name] = {
                "probability": round(alpha / total, 4),
                "confidence": round(1.0 - 1.0 / total, 4),
                "weight": bin_config.get("weight", 0.05),
                "sample_count": int(total - 2),  # 减去先验
                "bin": bin_label,
                "is_crypto_primary": bin_config.get("is_crypto_primary", False),
            }

        return results

    def query_crypto_primary_factors(
        self,
        strategy_id: str,
        symbol: str,
        factor_values: Dict[str, float],
        *,
        regime: str = "ranging",
    ) -> Dict[str, Dict[str, float]]:
        """仅查询加密一等因子（费率/OI/爆仓/稳定币流）。

        用于加密市场特定的决策辅助。
        """
        all_results = self.query_all_factors(
            strategy_id, symbol, factor_values, regime=regime,
        )
        return {
            k: v for k, v in all_results.items()
            if v.get("is_crypto_primary", False)
        }

    def get_aggregated_direction(
        self,
        strategy_id: str,
        symbol: str,
        factor_values: Dict[str, float],
        *,
        regime: str = "ranging",
        min_confidence: float = 0.3,
    ) -> Dict[str, Any]:
        """聚合所有因子输出方向性建议。

        Returns:
            {
                "direction": "long"|"short"|"neutral",
                "score": float,            # -1.0 (强空) ~ +1.0 (强多)
                "confidence": float,       # 聚合置信度
                "contributing_factors": [  # 有贡献的因子列表
                    {"name": "funding_rate", "probability": 0.72, "weight": 0.25, ...}
                ],
                "crypto_primary_score": float,  # 仅加密一等因子的得分
            }
        """
        all_results = self.query_all_factors(
            strategy_id, symbol, factor_values, regime=regime,
        )

        weighted_score = 0.0
        total_weight = 0.0
        crypto_score = 0.0
        crypto_weight = 0.0
        contributing = []

        for factor_name, result in all_results.items():
            prob = result["probability"]
            weight = result["weight"]
            conf = result["confidence"]

            if conf < min_confidence:
                continue  # 样本不足，跳过

            # 概率转方向: >0.5 偏多, <0.5 偏空
            factor_score = (prob - 0.5) * 2.0  # [-1, +1]

            weighted_score += factor_score * weight
            total_weight += weight

            if result.get("is_crypto_primary"):
                crypto_score += factor_score * weight
                crypto_weight += weight

            contributing.append({
                "name": factor_name,
                "probability": prob,
                "score": round(factor_score, 3),
                "weight": weight,
                "confidence": conf,
                "sample_count": result["sample_count"],
                "is_crypto_primary": result.get("is_crypto_primary", False),
            })

        # 归一化
        final_score = weighted_score / max(total_weight, 0.01)
        crypto_final = crypto_score / max(crypto_weight, 0.01)

        # 方向判定
        if final_score > 0.15:
            direction = "long"
        elif final_score < -0.15:
            direction = "short"
        else:
            direction = "neutral"

        # 聚合置信度：加权平均
        avg_confidence = (
            sum(c["confidence"] * c["weight"] for c in contributing) / max(total_weight, 0.01)
            if contributing else 0.0
        )

        return {
            "direction": direction,
            "score": round(final_score, 4),
            "confidence": round(min(avg_confidence, 1.0), 4),
            "contributing_factors": sorted(
                contributing, key=lambda x: abs(x["score"]) * x["weight"], reverse=True
            ),
            "crypto_primary_score": round(crypto_final, 4),
        }

    # ══════════════════════════════════════════════════
    #  策略因子权重管理
    # ══════════════════════════════════════════════════

    def update_strategy_factor_weights(
        self,
        strategy_id: str,
        factor_weights: Dict[str, float],
    ) -> None:
        """更新策略的因子权重（由进化/学习系统驱动）。"""
        with self._data_lock:
            self._strategy_factor_weights[strategy_id] = dict(factor_weights)
        logger.debug(f"[FactorJoint] 策略因子权重更新: {strategy_id}")

    def get_strategy_factor_weights(self, strategy_id: str) -> Dict[str, float]:
        """获取策略的因子权重。"""
        with self._data_lock:
            weights = self._strategy_factor_weights.get(strategy_id, {})
        if not weights:
            # 默认权重：从配置读取
            weights = {
                name: cfg["weight"]
                for name, cfg in FACTOR_BIN_CONFIG.items()
            }
        return weights

    # ══════════════════════════════════════════════════
    #  统计与诊断
    # ══════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """获取模型统计信息。"""
        with self._data_lock:
            total_keys = len(self._posteriors)
            total_updates = self._total_updates
            total_queries = self._total_queries

            # 各因子的样本量统计
            factor_samples = defaultdict(int)
            strategy_samples = defaultdict(int)
            for key, (alpha, beta) in self._posteriors.items():
                strategy_id, symbol, regime, factor_name, bin_label = key
                factor_samples[factor_name] += int(alpha + beta - 2)
                strategy_samples[strategy_id] += int(alpha + beta - 2)

        return {
            "total_posterior_entries": total_keys,
            "total_updates": total_updates,
            "total_queries": total_queries,
            "factor_samples": dict(factor_samples),
            "strategy_samples": dict(strategy_samples),
            "factors_configured": len(FACTOR_BIN_CONFIG),
            "regimes_configured": len(REGIME_BINS),
        }

    def reset_strategy(self, strategy_id: str) -> None:
        """重置某策略的所有后验（用于重新训练）。"""
        with self._data_lock:
            keys_to_remove = [
                k for k in self._posteriors if k[0] == strategy_id
            ]
            for k in keys_to_remove:
                del self._posteriors[k]
        logger.info(f"[FactorJoint] 策略后验已重置: {strategy_id} ({len(keys_to_remove)} entries)")

    # ══════════════════════════════════════════════════
    #  辅助方法
    # ══════════════════════════════════════════════════

    @staticmethod
    def _discretize(
        value: float, bins: List[float], labels: List[str]
    ) -> Optional[str]:
        """将连续值映射到离散 bin 标签。"""
        if value is None:
            return None
        for i, threshold in enumerate(bins):
            if value <= threshold:
                return labels[i]
        return labels[-1]  # 超出最大阈值 → 最后一个标签

    @staticmethod
    def _normalize_regime(regime: str) -> str:
        """规范化市况标签。"""
        regime_lower = (regime or "").lower().strip()
        # 映射到标准市况
        if any(kw in regime_lower for kw in ("bull", "trending", "expansion")):
            return "bull_market"
        elif any(kw in regime_lower for kw in ("bear", "crash")):
            return "bear_market"
        elif any(kw in regime_lower for kw in ("range", "sideway", "consolid")):
            return "ranging"
        elif any(kw in regime_lower for kw in ("volatil", "high_vol")):
            return "high_volatility"
        elif any(kw in regime_lower for kw in ("event", "news")):
            return "event_driven"
        return "ranging"

    def format_for_llm_prompt(
        self,
        strategy_id: str,
        symbol: str,
        factor_values: Dict[str, float],
        *,
        regime: str = "ranging",
        top_n: int = 5,
    ) -> str:
        """生成 LLM 提示词注入文本。

        仅输出样本量 ≥5 的因子，按 (|score| × weight) 排序。
        """
        aggregated = self.get_aggregated_direction(
            strategy_id, symbol, factor_values, regime=regime,
        )

        contributors = aggregated.get("contributing_factors", [])
        if not contributors:
            return ""

        # 过滤样本量不足的因子
        valid = [c for c in contributors if c.get("sample_count", 0) >= 5]
        if not valid:
            return ""

        lines = [
            "\n### 📊 因子-策略贝叶斯联合评估",
            f"- 聚合方向: {aggregated['direction'].upper()} "
            f"(得分 {aggregated['score']:+.3f}, 置信度 {aggregated['confidence']:.0%})",
        ]

        if aggregated.get("crypto_primary_score"):
            lines.append(
                f"- 加密一等因子方向: {'做多' if aggregated['crypto_primary_score'] > 0 else '做空'} "
                f"(得分 {aggregated['crypto_primary_score']:+.3f})"
            )

        lines.append("\n**最可靠的因子信号**（样本量≥5）：")
        for i, c in enumerate(valid[:top_n], 1):
            direction_icon = "↗" if c["score"] > 0 else "↘"
            crypto_tag = " 🔵" if c.get("is_crypto_primary") else ""
            lines.append(
                f"  {i}.{crypto_tag} {c['name']}: {direction_icon} "
                f"P(盈利)={c['probability']:.0%} "
                f"(样本{c['sample_count']}, 置信{c['confidence']:.0%})"
            )

        lines.append(
            "\n📌 决策要求：因子-策略贝叶斯联合评估方向与你的判断方向冲突时，"
            "必须在 reasoning 中给出具体反驳理由；无法给出则优先采纳贝叶斯方向。"
        )

        return "\n".join(lines)


# ══════════════════════════════════════════════════════
#  全局单例
# ══════════════════════════════════════════════════════

_joint_model_instance: Optional[FactorStrategyJointModel] = None


def get_factor_strategy_joint() -> FactorStrategyJointModel:
    """获取因子-策略贝叶斯联合模型单例。"""
    global _joint_model_instance
    if _joint_model_instance is None:
        _joint_model_instance = FactorStrategyJointModel()
    return _joint_model_instance
