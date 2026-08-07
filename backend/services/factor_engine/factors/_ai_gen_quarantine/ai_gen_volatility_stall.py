"""AI因子: 波动率停滞因子 | 置信:50% | 衡量短期波动率相对于长期波动率的异常变化，同时结合价格变化幅度。当短期波动率突然放大但价格几乎无涨跌（窄幅震荡）时，表明市场处于不确定状态（regime=unknown），因子输出负值；反之，如果价格伴随明确方向则正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityStallingFactor(BaseFactor):
    """衡量短期波动率相对于长期波动率的异常变化，同时结合价格变化幅度。当短期波动率突然放大但价格几乎无涨跌（窄幅震荡）时，表明市场处于不确定状态（regime=unknown），因子输出负值；反之，如果价格伴随明确方向则正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_stall",
            name="Volatility Stalling Factor",
            display_name="波动率停滞因子",
            description="衡量短期波动率相对于长期波动率的异常变化，同时结合价格变化幅度。当短期波动率突然放大但价格几乎无涨跌（窄幅震荡）时，表明市场处于不确定状态（regime=unknown），因子输出负值；反之，如果价格伴随明确方向则正值。",
            category="volatility",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high, low, close = data['high'], data['low'], data['close']
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        vol_ratio = atr_short / atr_long - 1  # 短期波动率相对变化
        price_change = close.pct_change(5).abs()  # 5日价格变化幅度
        # 当波动率放大但价格变化小时，负值
        raw = vol_ratio * (price_change - 0.02)  # 可能需调参
        # 裁剪并归一化
        return raw.clip(-1, 1)
