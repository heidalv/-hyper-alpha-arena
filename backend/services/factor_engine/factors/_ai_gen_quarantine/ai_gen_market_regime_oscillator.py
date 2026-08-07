"""AI因子: 市场状态震荡识别因子 | 置信:60% | 通过对比短期波动率与长期波动率，以及价格在布林带内的位置，判断市场处于趋势还是震荡。在震荡市场中，空头反转概率高，因子值偏向负值；趋势市场中因子值偏向正值。用于调整做空方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Marketregimeoscillator(BaseFactor):
    """通过对比短期波动率与长期波动率，以及价格在布林带内的位置，判断市场处于趋势还是震荡。在震荡市场中，空头反转概率高，因子值偏向负值；趋势市场中因子值偏向正值。用于调整做空方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_market_regime_oscillator",
            name="MarketRegimeOscillator",
            display_name="市场状态震荡识别因子",
            description="通过对比短期波动率与长期波动率，以及价格在布林带内的位置，判断市场处于趋势还是震荡。在震荡市场中，空头反转概率高，因子值偏向负值；趋势市场中因子值偏向正值。用于调整做空方向。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算20周期布林带宽度
        mid = data['close'].rolling(20).mean()
        std = data['close'].rolling(20).std()
        bb_width = (data['close'] - mid) / std  # 标准化位置
        # 计算短期波动率（5周期标准差）与长期波动率（20周期标准差）比值
        short_vol = data['close'].rolling(5).std()
        long_vol = data['close'].rolling(20).std()
        vol_ratio = short_vol / (long_vol + 1e-10)
        # 震荡特征：布林带宽度绝对值小且波动率比值低
        osc_score = -abs(bb_width) * (1 - vol_ratio.clip(0,1))
        # 归一化到[-1,1]
        result = osc_score / (osc_score.abs().mean() + 1e-10)
        result = result.clip(-1, 1)
        return result.fillna(0.0)
