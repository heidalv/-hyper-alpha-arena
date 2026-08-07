"""AI因子: 多空失衡均值回复 | 置信:60% | 识别价格在近期高低点附近的多空失衡，当价格接近区间极值且成交量异常时产生反转信号，避免在趋势不明时追涨杀跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ImbalanceMeanReversion(BaseFactor):
    """识别价格在近期高低点附近的多空失衡，当价格接近区间极值且成交量异常时产生反转信号，避免在趋势不明时追涨杀跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_imbrm",
            name="ImbalanceMeanReversion",
            display_name="多空失衡均值回复",
            description="识别价格在近期高低点附近的多空失衡，当价格接近区间极值且成交量异常时产生反转信号，避免在趋势不明时追涨杀跌。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high'].rolling(20).max()
        low = data['low'].rolling(20).min()
        volume = data['volume']
        # 价格在区间内的位置
        range_pos = (close - low) / (high - low + 1e-8)
        # 成交量相对均值
        vol_ratio = volume / volume.rolling(20).mean()
        # 当价格靠近上轨且成交量放大时，看空；靠近下轨且成交量放大时，看多
        upper_signal = (range_pos > 0.9) & (vol_ratio > 1.5)
        lower_signal = (range_pos < 0.1) & (vol_ratio > 1.5)
        # 信号强度：距离极值的远近
        upper_strength = (range_pos - 0.9) / 0.1  # 0~1
        lower_strength = (0.1 - range_pos) / 0.1
        result = np.where(upper_signal, -upper_strength, 0)
        result = np.where(lower_signal, lower_strength, result)
        return pd.Series(np.clip(result, -1, 1), index=data.index).fillna(0)
