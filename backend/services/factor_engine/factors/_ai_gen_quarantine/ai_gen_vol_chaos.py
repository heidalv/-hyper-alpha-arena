"""AI因子: 波动混沌指标 | 置信:60% | 通过计算近期价格波动相对于长期波动的异常程度，识别市场处于无规律、不可预测的混沌状态。当短期波动率远高于长期均值时，认为市场处于未知混乱期，因子值为负；当波动率稳定时为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Chaos_Indicator(BaseFactor):
    """通过计算近期价格波动相对于长期波动的异常程度，识别市场处于无规律、不可预测的混沌状态。当短期波动率远高于长期均值时，认为市场处于未知混乱期，因子值为负；当波动率稳定时为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_chaos",
            name="Volatility Chaos Indicator",
            display_name="波动混沌指标",
            description="通过计算近期价格波动相对于长期波动的异常程度，识别市场处于无规律、不可预测的混沌状态。当短期波动率远高于长期均值时，认为市场处于未知混乱期，因子值为负；当波动率稳定时为正。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算收益率
        ret = data['close'].pct_change()
        # 短期波动率（5期）与长期波动率（20期）的比值
        short_vol = ret.rolling(5).std()
        long_vol = ret.rolling(20).std()
        ratio = short_vol / (long_vol + 1e-10)
        # 异常阈值：比值超过2倍视为混沌
        chaos = (ratio - 1.5) * 2  # 映射到[-1,1]大致范围
        result = chaos.clip(-1, 1)
        return result
