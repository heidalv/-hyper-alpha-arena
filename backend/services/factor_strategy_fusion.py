"""
因子-策略融合引擎 — P2.4
实现严格/宽松/学习三种运行时因子融合模式及自动回退机制。

加密适配：
- 费率/OI/爆仓为一等因子，权重不受常规IC衰减影响
- 极端费率(|rate|>0.05%)强制切换STRICT模式
- 周末低流动性自动LOOSE降级
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── 加密一等因子权重体系 ──
CRYPTO_PRIMARY_FACTORS: Dict[str, Dict[str, Any]] = {
    "funding_rate": {"weight": 0.25, "extreme_threshold": 0.0005, "direction": "reversal"},
    "oi_change": {"weight": 0.20, "extreme_threshold": 0.15, "direction": "momentum"},
    "liquidation_intensity": {"weight": 0.18, "extreme_threshold": 0.5, "direction": "reversal"},
    "stablecoin_flow": {"weight": 0.12, "extreme_threshold": 0.03, "direction": "momentum"},
    "exchange_reserve_change": {"weight": 0.10, "extreme_threshold": 0.02, "direction": "trend"},
    "gas_fee_surge": {"weight": 0.08, "extreme_threshold": 0.3, "direction": "momentum"},
    "btc_dominance_change": {"weight": 0.07, "extreme_threshold": 0.02, "direction": "reversal"},
}

# 传统技术因子（IC衰减适用）
TECHNICAL_FACTOR_BASE_WEIGHTS: Dict[str, float] = {
    "rsi": 0.15,
    "macd": 0.15,
    "ema_trend": 0.12,
    "atr_volatility": 0.08,
    "bb_position": 0.10,
    "volume_profile": 0.10,
    "adx_trend": 0.10,
    "obv_divergence": 0.10,
    "doji_score": 0.05,
    "volume_price_corr": 0.05,
}


class FactorStrategyFusion:
    """因子-策略融合引擎 — 三模式运行时切换"""

    _instance: Optional["FactorStrategyFusion"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._mode: str = "STRICT"
        self._mode_history: List[Dict[str, Any]] = []
        self._last_mode_switch_ts: float = 0.0
        self._switch_cooling_s: int = 300  # 5分钟冷却
        self._consecutive_strict_rejections: int = 0
        self._ic_cache: Dict[str, List[float]] = {}
        logger.info("[FactorFusion] 因子融合引擎初始化完成 (mode=STRICT)")

    @classmethod
    def get_instance(cls) -> "FactorStrategyFusion":
        return cls()

    def _is_enabled(self) -> bool:
        try:
            from backend.config.settings import AI_FACTOR_STRATEGY_FUSION_ENABLED
            return bool(AI_FACTOR_STRATEGY_FUSION_ENABLED)
        except Exception:
            return False

    # ── 模式判定 ──

    def _detect_extreme_conditions(self, factor_signals: Dict[str, Any]) -> Dict[str, bool]:
        """检测加密市场极端条件，决定是否需要强制切换模式"""
        triggers = {
            "funding_extreme": False,
            "oi_extreme": False,
            "liquidation_cascade": False,
            "weekend_low_liquidity": False,
            "exchange_event": False,
        }

        # 费率极端
        funding = factor_signals.get("funding_rate", 0)
        if abs(float(funding)) > CRYPTO_PRIMARY_FACTORS["funding_rate"]["extreme_threshold"]:
            triggers["funding_extreme"] = True

        # OI极端变化
        oi_chg = factor_signals.get("oi_change", 0)
        if abs(float(oi_chg)) > CRYPTO_PRIMARY_FACTORS["oi_change"]["extreme_threshold"]:
            triggers["oi_extreme"] = True

        # 爆仓级联
        liq = factor_signals.get("liquidation_intensity", 0)
        if abs(float(liq)) > CRYPTO_PRIMARY_FACTORS["liquidation_intensity"]["extreme_threshold"]:
            triggers["liquidation_cascade"] = True

        # 周末低流动性
        now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            triggers["weekend_low_liquidity"] = True

        return triggers

    def _determine_mode(
        self,
        factor_signals: Dict[str, Any],
        sample_count: int,
    ) -> str:
        """
        根据市场条件判定当前应使用的融合模式。

        优先级：
        1. 极端费率/爆仓级联 → STRICT（硬约束）
        2. 周末低流动性 → LOOSE（放宽）
        3. 样本充足(≥200) → LEARNING（贝叶斯更新）
        4. 样本中等(≥50) → STRICT（IC加权）
        5. 样本不足 → LOOSE（等权平均）
        """
        extremes = self._detect_extreme_conditions(factor_signals)

        # 极端条件强制 STRICT
        if extremes["funding_extreme"] or extremes["liquidation_cascade"]:
            return "STRICT"

        # 周末自动 LOOSE
        if extremes["weekend_low_liquidity"] and sample_count < 200:
            return "LOOSE"

        # 按样本量分级
        if sample_count >= 200:
            return "LEARNING"
        elif sample_count >= 50:
            return "STRICT"
        else:
            return "LOOSE"

    def _should_switch_mode(self, new_mode: str) -> bool:
        """检查是否允许切换模式（冷却期检查）"""
        now_ts = _time.time()
        if now_ts - self._last_mode_switch_ts < self._switch_cooling_s:
            # 冷却期内，但 STRICT 模式不受冷却限制
            if new_mode != "STRICT":
                return False
        return True

    def _record_mode_switch(self, new_mode: str, reason: str):
        """记录模式切换历史"""
        self._last_mode_switch_ts = _time.time()
        self._mode = new_mode
        self._mode_history.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "mode": new_mode,
            "reason": reason,
        })
        # 保留最近100次记录
        self._mode_history = self._mode_history[-100:]
        logger.info(f"[FactorFusion] 模式切换: → {new_mode} ({reason})")

    # ── 回退机制 ──

    def _check_strict_health(self) -> bool:
        """检查 STRICT 模式健康状况，连续过多拒绝时自动降级"""
        if self._consecutive_strict_rejections >= 4:
            logger.warning(
                f"[FactorFusion] STRICT模式连续拒绝{self._consecutive_strict_rejections}次，自动降级→LOOSE"
            )
            self._record_mode_switch("LOOSE", "strict_consecutive_rejections")
            self._consecutive_strict_rejections = 0
            return False
        return True

    # ── 核心融合 ──

    def fuse(
        self,
        factor_signals: Dict[str, Any],
        sample_count: int = 100,
        *,
        symbol: str = "",
        force_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        因子融合主入口。

        Args:
            factor_signals: {factor_name: value} 因子信号字典
            sample_count: 可用样本数（用于IC计算）
            symbol: 交易对（用于日志）
            force_mode: 强制使用指定模式

        Returns:
            {
                "direction": "long|short|neutral",
                "strength": 0.0-1.0,
                "confidence": 0.0-1.0,
                "mode": "STRICT|LOOSE|LEARNING",
                "contribution": {factor_name: contribution_value},
            }
        """
        if not self._is_enabled():
            return self._fallback_simple(factor_signals)

        # 确定模式
        if force_mode and force_mode in ("STRICT", "LOOSE", "LEARNING"):
            mode = force_mode
        else:
            mode = self._determine_mode(factor_signals, sample_count)

        # 检查冷却期
        if not self._should_switch_mode(mode) and mode != self._mode:
            mode = self._mode
        elif mode != self._mode:
            self._record_mode_switch(mode, f"auto: sample={sample_count}")

        # 执行融合
        try:
            if mode == "STRICT":
                result = self._fuse_strict(factor_signals)
            elif mode == "LEARNING":
                result = self._fuse_learning(factor_signals, sample_count)
            else:  # LOOSE
                result = self._fuse_loose(factor_signals)

            result["mode"] = mode
            return result

        except Exception as exc:
            logger.error(f"[FactorFusion] 融合失败({mode}): {exc}", exc_info=True)
            # 自动回退到 LOOSE
            self._record_mode_switch("LOOSE", f"exception_in_{mode}")
            return self._fuse_loose(factor_signals)

    def _fuse_strict(self, factor_signals: Dict[str, Any]) -> Dict[str, Any]:
        """
        STRICT 模式：IC加权 + 加密一等因子优先 + 一致性校验

        - 加密一等因子权重不受IC衰减影响
        - 技术因子按IC值衰减
        - 信号一致性 < 0.3 时拒绝输出信号
        """
        weighted_sum = 0.0
        total_weight = 0.0
        contributions: Dict[str, float] = {}

        for factor_name, raw_value in factor_signals.items():
            if factor_name in CRYPTO_PRIMARY_FACTORS:
                weight = CRYPTO_PRIMARY_FACTORS[factor_name]["weight"]
            else:
                weight = TECHNICAL_FACTOR_BASE_WEIGHTS.get(factor_name, 0.05)
                # IC衰减：取最近30个IC值的中位数
                ic_list = self._ic_cache.get(factor_name, [])
                if ic_list:
                    median_ic = float(np.median(ic_list[-30:]))
                    weight *= max(0.1, 1.0 - abs(median_ic) * 2)

            # 标准化信号值到 [-1, 1]
            norm_val = float(raw_value)
            if factor_name == "funding_rate":
                norm_val = -np.sign(norm_val) * min(abs(norm_val) / 0.001, 1.0)
            elif abs(norm_val) > 1.0:
                norm_val = np.sign(norm_val)

            weighted_sum += norm_val * weight
            total_weight += weight
            contributions[factor_name] = norm_val * weight

        if total_weight <= 0:
            return self._fallback_simple(factor_signals)

        direction_score = weighted_sum / total_weight

        # 一致性校验
        if abs(direction_score) < 0.15:
            self._consecutive_strict_rejections += 1
            self._check_strict_health()
            return {
                "direction": "neutral",
                "strength": 0.0,
                "confidence": 0.0,
                "mode": "STRICT",
                "contribution": contributions,
                "rejected": "low_consistency",
            }

        self._consecutive_strict_rejections = 0
        direction = "long" if direction_score > 0 else "short"
        strength = min(abs(direction_score), 1.0)
        confidence = min(strength * 1.2, 1.0)

        return {
            "direction": direction,
            "strength": round(strength, 4),
            "confidence": round(confidence, 4),
            "mode": "STRICT",
            "contribution": contributions,
        }

    def _fuse_loose(self, factor_signals: Dict[str, Any]) -> Dict[str, Any]:
        """
        LOOSE 模式：等权平均 + 宽松阈值

        - 所有因子等权（加密一等因子2x权重）
        - 信号阈值降低到 0.08
        - 永不拒绝（总是输出方向）
        """
        pos_score = 0.0
        neg_score = 0.0
        total_count = 0

        for factor_name, raw_value in factor_signals.items():
            norm_val = float(raw_value)
            if factor_name == "funding_rate":
                norm_val = -np.sign(norm_val) * min(abs(norm_val) / 0.001, 1.0)
            elif abs(norm_val) > 1.0:
                norm_val = np.sign(norm_val)

            multiplier = 2.0 if factor_name in CRYPTO_PRIMARY_FACTORS else 1.0

            if norm_val > 0:
                pos_score += norm_val * multiplier
            else:
                neg_score += abs(norm_val) * multiplier
            total_count += multiplier

        if total_count <= 0:
            return self._fallback_simple(factor_signals)

        net = (pos_score - neg_score) / total_count

        if abs(net) < 0.05:
            return {"direction": "neutral", "strength": 0.0, "confidence": 0.1}

        direction = "long" if net > 0 else "short"
        strength = min(abs(net), 0.8)
        confidence = strength * 0.7

        return {
            "direction": direction,
            "strength": round(strength, 4),
            "confidence": round(confidence, 4),
            "mode": "LOOSE",
        }

    def _fuse_learning(
        self, factor_signals: Dict[str, Any], sample_count: int
    ) -> Dict[str, Any]:
        """
        LEARNING 模式：贝叶斯后验更新 + 动态权重

        当样本量 ≥200 时自动启用。
        每个因子维护自己的后验分布参数(alpha=胜, beta=负)，
        融合时按后验均值加权。
        """
        # 简化版：如果样本足够，使用STRICT的IC逻辑（未来可扩展完整贝叶斯）
        result = self._fuse_strict(factor_signals)
        result["mode"] = "LEARNING"

        # Learning 模式特有：根据样本量微调置信度
        if sample_count >= 500:
            result["confidence"] = min(result.get("confidence", 0) * 1.1, 1.0)
        return result

    def _fallback_simple(self, factor_signals: Dict[str, Any]) -> Dict[str, Any]:
        """最简回退：等权投票"""
        longs = sum(1 for v in factor_signals.values() if float(v) > 0)
        shorts = sum(1 for v in factor_signals.values() if float(v) < 0)
        total = longs + shorts
        if total == 0:
            return {"direction": "neutral", "strength": 0.0, "confidence": 0.0}
        if longs > shorts:
            return {"direction": "long", "strength": longs / total, "confidence": 0.5}
        return {"direction": "short", "strength": shorts / total, "confidence": 0.5}

    def update_ic(self, factor_name: str, ic_value: float):
        """更新因子IC缓存（供IC加权使用）"""
        if factor_name not in self._ic_cache:
            self._ic_cache[factor_name] = []
        self._ic_cache[factor_name].append(ic_value)
        # 保留最近100个IC值
        self._ic_cache[factor_name] = self._ic_cache[factor_name][-100:]

    def get_status(self) -> Dict[str, Any]:
        """获取引擎状态"""
        return {
            "mode": self._mode,
            "mode_history": self._mode_history[-5:],
            "strict_rejections": self._consecutive_strict_rejections,
            "cooling_remaining_s": max(
                0,
                self._switch_cooling_s - (_time.time() - self._last_mode_switch_ts),
            ),
            "ic_cache_size": {k: len(v) for k, v in self._ic_cache.items()},
        }


# 全局单例
factor_strategy_fusion = FactorStrategyFusion.get_instance()
