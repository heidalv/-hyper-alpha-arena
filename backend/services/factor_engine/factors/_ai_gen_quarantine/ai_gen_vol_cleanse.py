"""AI因子: 波动清洗因子 | 置信:60% | 衡量价格波动与成交量波动的背离程度。当价格窄幅震荡但成交量异常放大时，预示市场清理假突破，之后可能出现反转。因子值为正时建议做多，负时建议做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityCleanse(BaseFactor):
    """衡量价格波动与成交量波动的背离程度。当价格窄幅震荡但成交量异常放大时，预示市场清理假突破，之后可能出现反转。因子值为正时建议做多，负时建议做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_cleanse",
            name="Volatility Cleanse",
            display_name="波动清洗因子",
            description="衡量价格波动与成交量波动的背离程度。当价格窄幅震荡但成交量异常放大时，预示市场清理假突破，之后可能出现反转。因子值为正时建议做多，负时建议做空。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算过去20周期价格波动率（ATR/收盘价）
        atr = (data['high'] - data['low']).rolling(20).mean()
        price_vol = atr / data['close']
        # 计算过去20周期成交量波动率（成交量标准差/均值）
        vol_std = data['volume'].rolling(20).std()
        vol_mean = data['volume'].rolling(20).mean()
        volume_vol = vol_std / vol_mean
        # 比值：价格波动小，成交量波动大时比值小
        ratio = price_vol / (volume_vol + 1e-10)
        # 标准化为[-1,1]，用z-score再tanh
        z = (ratio - ratio.rolling(60).mean()) / (ratio.rolling(60).std() + 1e-10)
        result = -np.tanh(z)  # 负相关：比值低（清洗）时为正信号
        return result.fillna(0)
