"""AI因子: 震荡市判别 | 置信:60% | 基于价格区间波动率与趋势强度的比值，识别无明显趋势的震荡市（regime unknown）。震荡市容易导致趋势策略持仓超时或止损，信号负值表示强震荡应避免交易，正值表示趋势环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class OscillationRegimeIndicator(BaseFactor):
    """基于价格区间波动率与趋势强度的比值，识别无明显趋势的震荡市（regime unknown）。震荡市容易导致趋势策略持仓超时或止损，信号负值表示强震荡应避免交易，正值表示趋势环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_oscillation_regime",
            name="Oscillation Regime Indicator",
            display_name="震荡市判别",
            description="基于价格区间波动率与趋势强度的比值，识别无明显趋势的震荡市（regime unknown）。震荡市容易导致趋势策略持仓超时或止损，信号负值表示强震荡应避免交易，正值表示趋势环境。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        period = 20
        # 波动区间：过去 period 最高最低之差
        range_high = high.rolling(window=period).max()
        range_low = low.rolling(window=period).min()
        price_range = range_high - range_low
        # 价格位置：当前价格在区间内的位置 (0~1)
        position = (close - range_low) / price_range.replace(0, np.nan)
        # 趋势强度：线性回归斜率
        x = np.arange(period)
        def slope(series):
            if len(series) < period:
                return np.nan
            y = series.values
            return np.polyfit(x, y, 1)[0]
        slope_series = close.rolling(window=period).apply(slope, raw=False)
        # 归一化斜率
        atr = (high - low).rolling(window=period).mean()
        norm_slope = slope_series / atr.replace(0, np.nan)
        # 震荡强度：价格在区间中部反复穿越，用位置变化的自相关或标准差
        position_std = position.rolling(window=period).std()
        # 合成：低斜率 + 高位置波动 -> 震荡
        oscillation = -norm_slope.abs() + position_std
        # 标准化到[-1,1]
        result = oscillation.rolling(window=10).mean()
        result = (result - result.rolling(window=100).mean()) / result.rolling(window=100).std()
        result = result.clip(-1, 1)
        return result
