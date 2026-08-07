"""AI因子: 窄幅震荡识别 | 置信:70% | 通过比较当前价格范围与ATR的比值，识别窄幅震荡行情。当价格在窄幅区间内波动时输出负值，提示趋势弱、容易止损；当突破区间时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Narrow_Range_Oscillator(BaseFactor):
    """通过比较当前价格范围与ATR的比值，识别窄幅震荡行情。当价格在窄幅区间内波动时输出负值，提示趋势弱、容易止损；当突破区间时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_narrow_range",
            name="Narrow Range Oscillator",
            display_name="窄幅震荡识别",
            description="通过比较当前价格范围与ATR的比值，识别窄幅震荡行情。当价格在窄幅区间内波动时输出负值，提示趋势弱、容易止损；当突破区间时输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        atr = (high.rolling(14).max() - low.rolling(14).min()) / close.rolling(14).mean()  # 归一化ATR
        current_range = (high - low) / close
        ratio = current_range / atr
        # 标准化到[-1,1]，当ratio远小于1时窄幅震荡，输出负值
        norm = (ratio - ratio.rolling(20).mean()) / ratio.rolling(20).std()
        result = np.clip(norm * (-1), -1, 1)  # 窄幅震荡 -> 负值
        return result.fillna(0)
