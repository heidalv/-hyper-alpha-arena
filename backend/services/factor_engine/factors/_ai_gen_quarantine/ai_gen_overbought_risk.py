"""AI因子: 超买风险因子 | 置信:55% | 衡量价格相对20日均线的偏离度，结合ATR归一化。当收盘价高于均线超过2倍ATR时视为超买区域，做多回调风险大，因子输出负值；低于-2倍ATR时视为超卖区域可做多，输出正值。实盘亏损多发生在regime unknown时的盲目做多，本因子可防止追高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Overbought_Risk_Factor(BaseFactor):
    """衡量价格相对20日均线的偏离度，结合ATR归一化。当收盘价高于均线超过2倍ATR时视为超买区域，做多回调风险大，因子输出负值；低于-2倍ATR时视为超卖区域可做多，输出正值。实盘亏损多发生在regime unknown时的盲目做多，本因子可防止追高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_overbought_risk",
            name="Overbought Risk Factor",
            display_name="超买风险因子",
            description="衡量价格相对20日均线的偏离度，结合ATR归一化。当收盘价高于均线超过2倍ATR时视为超买区域，做多回调风险大，因子输出负值；低于-2倍ATR时视为超卖区域可做多，输出正值。实盘亏损多发生在regime unknown时的盲目做多，本因子可防止追高。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        period = 20
        ma = close.rolling(period).mean()
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(period).mean()
        dev = (close - ma) / atr
        # 将dev映射到[-1,1]，超买负值，超卖正值
        result = -np.sign(dev) * np.minimum(np.abs(dev) / 2.0, 1.0)
        return result
