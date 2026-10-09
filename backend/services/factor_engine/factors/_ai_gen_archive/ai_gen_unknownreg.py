"""AI因子: 未知行情空头信号 | 置信:50% | 所有亏损都在regime=unknown，且空头亏损次数和幅度更大（SOL/UNI/VIRTUAL）。该因子检测市场处于无明确趋势状态（价格围绕均线反复穿越且波动率低），此时做空风险更高，给出负向信号以抑制空头入场。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeShortSignal(BaseFactor):
    """所有亏损都在regime=unknown，且空头亏损次数和幅度更大（SOL/UNI/VIRTUAL）。该因子检测市场处于无明确趋势状态（价格围绕均线反复穿越且波动率低），此时做空风险更高，给出负向信号以抑制空头入场。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknownreg",
            name="Unknown_Regime_Short_Signal",
            display_name="未知行情空头信号",
            description="所有亏损都在regime=unknown，且空头亏损次数和幅度更大（SOL/UNI/VIRTUAL）。该因子检测市场处于无明确趋势状态（价格围绕均线反复穿越且波动率低），此时做空风险更高，给出负向信号以抑制空头入场。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma20 = data['close'].rolling(20).mean()
        ma50 = data['close'].rolling(50).mean()
        trend_diff = (ma20 - ma50).abs() / (data['close'] + 1e-9)
        vol = data['close'].pct_change().rolling(20).std()
        vol_ratio = vol / (data['close'].pct_change().rolling(50).std() + 1e-9)
        cross_rate = (data['close'] > ma20).astype(float).diff().abs().rolling(10).sum() / 10
        unknown = ((trend_diff < 0.01) & (vol_ratio < 1.2)).astype(float)
        result = -1 * (unknown * (1 - cross_rate)).clip(-1, 1)
        return result.fillna(0)
