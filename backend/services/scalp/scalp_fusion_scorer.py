"""ScalpFusionScorer — 短线因子 × AI周期概率引擎 融合打分。

背景
----
`ScalpFactorRouter` 的短线决策此前完全是规则因子链，从不使用任何 AI/统计模型判断方向
（唯一的 LLM 触点是 35-44 分边缘信号的 5 秒 Flash Veto，且看不到因子明细）。

项目里已经有一套训练好、但只接给了 mid/long 用的统计模型：`CycleProbabilityEngine`
（见 `backend/services/cycle_direction_probability.py`），用加权朴素贝叶斯从历史K线学出
"给定当前技术特征，未来涨/跌/震荡的概率"，且已经训练了 short tier（对应 15m 主周期）。
该模型查表成本 <1ms、不调 LLM，完全符合短线延迟预算，是给短线补一层"AI 第二意见"的
天然低成本入口。

融合方式
--------
1. 复用短线路由器已经在算的 15m K线（`market_data["klines_15m"]`），
   用 `cycle_direction_probability.build_feature_series` 算出最后一根的技术特征；
2. 调 `cycle_probability_engine.estimate("short", features)` 拿到 prob_up/prob_down/
   calibration_quality；
3. `net_prob = prob_up - prob_down`；`agreement = net_prob × 因子方向符号`
   （agreement>0 表示 AI 支持因子方向，<0 表示反对）；
4. `weighted_agreement = agreement × calibration_quality`
   —— 校准感知：当前 short tier 校准质量约0.05（历史数据仅约7天），
   这一步天然让 AI 的影响很小，随着数据积累、模型重训、校准质量提升，
   影响会自动变大，无需手工调权重（沿用项目里 cycle_prob 接入 mid/long 时
   已经验证过的"谦虚→随数据积累增强话语权"设计）；
5. `delta = clamp(weighted_agreement × SCALP_FUSION_MAX_DELTA, ±MAX_DELTA)`，
   直接加到 `ScalpFactorRouter` 的 `factor_score` 上，自然传导进
   `ScalpExecutionGate` 的分层门禁，不需要改门禁逻辑本身。

本模块不下单、不调 LLM，纯查表+numpy运算，任何异常/数据不足都安全降级为 delta=0
（不影响原有行为）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from backend.config.settings import (
    SCALP_FUSION_ENABLED,
    SCALP_FUSION_MAX_DELTA,
    SCALP_FUSION_MIN_CALIBRATION,
)

logger = logging.getLogger(__name__)


@dataclass
class FusionResult:
    """融合打分结果。"""
    delta: int = 0
    available: bool = False
    breakdown: Dict[str, Any] = field(default_factory=dict)


class ScalpFusionScorer:
    """把 CycleProbabilityEngine(short tier) 的方向概率按校准质量融合进短线因子分数。"""

    def compute_fusion_adjustment(
        self, direction: str, klines_15m_df: Any,
    ) -> FusionResult:
        """计算 AI 概率引擎对短线因子分数的调整量。

        Args:
            direction: 因子路由器当前判定的方向，long/short/neutral。
            klines_15m_df: 15m K线（DataFrame 或可转换为 DataFrame 的结构），
                需含 high/low/close/volume 列。

        Returns:
            FusionResult: delta（可正可负，已 clamp 在 ±SCALP_FUSION_MAX_DELTA 内）
                + available（cycle_prob 模型是否成功给出估计） + breakdown（明细，
                用于日志/审计/喂给 Flash Veto prompt）。
        """
        if not SCALP_FUSION_ENABLED:
            return FusionResult()
        if direction not in ("long", "short"):
            return FusionResult(breakdown={"cycle_prob_reason": "方向中性，跳过融合"})
        if klines_15m_df is None or not hasattr(klines_15m_df, "__len__") or len(klines_15m_df) < 30:
            return FusionResult(breakdown={"cycle_prob_reason": "15m K线不足30根，跳过融合"})

        try:
            import numpy as np
            import pandas as pd

            from backend.services.cycle_direction_probability import (
                FEATURES,
                build_feature_series,
                cycle_probability_engine,
            )

            df = (
                klines_15m_df
                if isinstance(klines_15m_df, pd.DataFrame)
                else pd.DataFrame(klines_15m_df)
            )
            for col in ("high", "low", "close", "volume"):
                if col not in df.columns:
                    return FusionResult(
                        breakdown={"cycle_prob_reason": f"缺少{col}列，跳过融合"}
                    )

            feat_series = build_feature_series({
                "high": df["high"].to_numpy(dtype=float),
                "low": df["low"].to_numpy(dtype=float),
                "close": df["close"].to_numpy(dtype=float),
                "volume": df["volume"].to_numpy(dtype=float),
            })

            last_feats: Dict[str, float] = {}
            for f in FEATURES:
                arr = feat_series.get(f)
                if arr is None or len(arr) == 0:
                    continue
                v = arr[-1]
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    continue
                last_feats[f] = float(v)

            if not last_feats:
                return FusionResult(breakdown={"cycle_prob_reason": "特征全部无效，跳过融合"})

            result = cycle_probability_engine.estimate("short", last_feats)
            if not result.available:
                return FusionResult(
                    breakdown={"cycle_prob_reason": result.reason or "short模型未加载/未训练"}
                )

            calibration = float(result.calibration_quality or 0.0)
            if calibration < SCALP_FUSION_MIN_CALIBRATION:
                return FusionResult(
                    available=True,
                    breakdown={
                        "cycle_prob_reason": (
                            f"校准质量{calibration:.3f}<"
                            f"{SCALP_FUSION_MIN_CALIBRATION}，跳过融合"
                        ),
                        "cycle_prob_calibration": round(calibration, 4),
                    },
                )

            net_prob = float(result.prob_up - result.prob_down)
            request_dir = 1.0 if direction == "long" else -1.0
            agreement = net_prob * request_dir
            weighted_agreement = agreement * calibration
            raw_delta = weighted_agreement * float(SCALP_FUSION_MAX_DELTA)
            delta = int(round(max(-SCALP_FUSION_MAX_DELTA, min(SCALP_FUSION_MAX_DELTA, raw_delta))))

            breakdown = {
                "cycle_prob_dir": result.direction,
                "cycle_prob_up": round(result.prob_up, 4),
                "cycle_prob_down": round(result.prob_down, 4),
                "cycle_prob_confidence": round(result.confidence, 4),
                "cycle_prob_calibration": round(calibration, 4),
                "cycle_prob_drivers": ",".join(result.top_drivers),
                "cycle_prob_agreement": round(agreement, 4),
                "cycle_prob_delta": delta,
            }
            return FusionResult(delta=delta, available=True, breakdown=breakdown)
        except Exception as exc:
            logger.debug("[ScalpFusion] 融合计算异常，安全降级为无影响: %s", exc)
            return FusionResult(breakdown={"cycle_prob_reason": f"异常降级:{exc}"})


# 全局单例
scalp_fusion_scorer = ScalpFusionScorer()
