"""AI因子: 伪趋势强度 | 置信:60% | 基于Kaufman效率比和波动率调整来衡量趋势的真实可靠性。当因子值接近+1时表明市场存在强趋势（方向明确且噪声低），接近-1时表明市场处于弱趋势或震荡（容易导致持仓超时或止损）。针对max_hold_timeout和sl亏损模式，低值区域应谨慎做趋势策略。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Pseudo_Trend_Strength(BaseFactor):
    """基于Kaufman效率比和波动率调整来衡量趋势的真实可靠性。当因子值接近+1时表明市场存在强趋势（方向明确且噪声低），接近-1时表明市场处于弱趋势或震荡（容易导致持仓超时或止损）。针对max_hold_timeout和sl亏损模式，低值区域应谨慎做趋势策略。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pseudotrend",
            name="Pseudo Trend Strength",
            display_name="伪趋势强度",
            description="基于Kaufman效率比和波动率调整来衡量趋势的真实可靠性。当因子值接近+1时表明市场存在强趋势（方向明确且噪声低），接近-1时表明市场处于弱趋势或震荡（容易导致持仓超时或止损）。针对max_hold_timeout和sl亏损模式，低值区域应谨慎做趋势策略。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        n = 10
        # 效率比
        price_change = close.diff(n).abs()
        volatility = close.diff().abs().rolling(n).sum()
        er = price_change / volatility  # 效率比，范围0~1
        # 波动率调整：低波动时趋势更不可靠
        atr = (data['high'] - data['low']).rolling(14).mean()
        atr_pct = atr / close
        vol_adj = 1 - (atr_pct / atr_pct.rolling(60).mean()).clip(0, 2) * 0.3  # 调整因子
        pseudo = er * vol_adj
        pseudo = pseudo * 2 - 1  # 映射到[-1,1]
        return pseudo.fillna(0)
