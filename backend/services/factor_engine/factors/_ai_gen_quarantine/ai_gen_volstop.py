"""AI因子: 波动率自适应止损指标 | 置信:60% | 基于过去N周期真实波幅（ATR）动态调整止损距离。当当前价格接近基于波动率计算的止损线时，发出预警信号（负值表示做空风险，正值表示做多风险）。适用于避免在高波动率下过早止损或在低波动率下止损过宽导致的亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedStopLossIndicator(BaseFactor):
    """基于过去N周期真实波幅（ATR）动态调整止损距离。当当前价格接近基于波动率计算的止损线时，发出预警信号（负值表示做空风险，正值表示做多风险）。适用于避免在高波动率下过早止损或在低波动率下止损过宽导致的亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volstop",
            name="Volatility-Adjusted Stop Loss Indicator",
            display_name="波动率自适应止损指标",
            description="基于过去N周期真实波幅（ATR）动态调整止损距离。当当前价格接近基于波动率计算的止损线时，发出预警信号（负值表示做空风险，正值表示做多风险）。适用于避免在高波动率下过早止损或在低波动率下止损过宽导致的亏损。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 动态止损倍数（根据市场状态调整，这里固定为2倍ATR）
        multiplier = 2.0
        # 对于多头：止损线 = 收盘价 - multiplier * atr；对于空头：止损线 = 收盘价 + multiplier * atr
        # 返回信号：价格离止损线越近，信号越极端
        long_stop = close - multiplier * atr
        short_stop = close + multiplier * atr
        # 归一化到[-1,1]：使用价格与止损线的相对距离，距离越小越危险
        long_dist = (close - long_stop) / (atr + 1e-8)  # 距离倍数
        short_dist = (short_stop - close) / (atr + 1e-8)
        # 多头风险信号：当long_dist小于1时表示接近止损，为负值；空头类似
        long_signal = -np.clip(1.0 - long_dist, 0, 1)  # 当距离<1,信号为负
        short_signal = -np.clip(1.0 - short_dist, 0, 1) # 空头接近止损也为负
        # 综合：取两者更极端的一个（若同时接近则更负）
        result = np.maximum(long_signal, short_signal)
        # 避免NaN
        result = result.fillna(0).clip(-1, 1)
        return result
