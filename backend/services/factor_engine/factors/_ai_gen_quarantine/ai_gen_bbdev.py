"""AI因子: 布林带偏离做多风险因子 | 置信:60% | 计算当前收盘价相对于20日布林带上轨的偏离度，结合价格位置和波动率，识别超买风险。当价格突破上轨且偏离超过1.5倍带宽时发出负信号，避免在极端高位做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Band_Deviation_for_Long_Risk(BaseFactor):
    """计算当前收盘价相对于20日布林带上轨的偏离度，结合价格位置和波动率，识别超买风险。当价格突破上轨且偏离超过1.5倍带宽时发出负信号，避免在极端高位做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bbdev",
            name="Bollinger Band Deviation for Long Risk",
            display_name="布林带偏离做多风险因子",
            description="计算当前收盘价相对于20日布林带上轨的偏离度，结合价格位置和波动率，识别超买风险。当价格突破上轨且偏离超过1.5倍带宽时发出负信号，避免在极端高位做多。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns ['open','high','low','close','volume']
        close = data['close']
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_band = sma20 + 2 * std20
        # 偏离度：价格相对于上轨的位置，归一化到[-1,1]
        deviation = (close - upper_band) / (std20 + 1e-10)
        # 当偏离>1.5时视为极端风险，信号为负；否则在0附近
        result = -1.0 * (deviation.clip(0, 3) / 3.0)
        # 确保结果在[-1,1]
        return result.clip(-1, 1)
