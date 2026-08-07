"""AI因子: 趋势强度波动调整 | 置信:65% | 通过计算收盘价相对于20周期移动平均的斜率与平均真实波幅(ATR)的比值，衡量趋势强度与波动率的匹配程度。当趋势弱而波动率大时（易止损），因子接近-1；趋势强且波动率低时（易止盈过早），因子接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthVolatility(BaseFactor):
    """通过计算收盘价相对于20周期移动平均的斜率与平均真实波幅(ATR)的比值，衡量趋势强度与波动率的匹配程度。当趋势弱而波动率大时（易止损），因子接近-1；趋势强且波动率低时（易止盈过早），因子接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_vol",
            name="TrendStrengthVolatility",
            display_name="趋势强度波动调整",
            description="通过计算收盘价相对于20周期移动平均的斜率与平均真实波幅(ATR)的比值，衡量趋势强度与波动率的匹配程度。当趋势弱而波动率大时（易止损），因子接近-1；趋势强且波动率低时（易止盈过早），因子接近+1。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 移动平均
        ma = close.rolling(20).mean()
        # 斜率：当前close相对于ma的百分比偏离
        slope = (close - ma) / ma
        # 计算ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 波动率调整：将斜率除以ATR与价格的比例，避免量纲影响
        norm_slope = slope / (atr / close) * 100
        # 用tanh压缩到[-1,1]
        result = np.tanh(norm_slope * 0.1)
        return result
