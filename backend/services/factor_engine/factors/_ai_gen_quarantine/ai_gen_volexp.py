"""AI因子: 波动率爆发检测 | 置信:65% | 通过比较当前短期波动率与历史波动率的比值，识别波动率突然放大的时刻。当短期波动率远高于近期平均水平时，因子值为负（表示高风险，不适合做多）；当波动率收缩时，因子值为正（表示市场平稳，适合趋势策略）。可有效过滤掉‘max_hold_timeout’和‘sl’类亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Explosion_Detector(BaseFactor):
    """通过比较当前短期波动率与历史波动率的比值，识别波动率突然放大的时刻。当短期波动率远高于近期平均水平时，因子值为负（表示高风险，不适合做多）；当波动率收缩时，因子值为正（表示市场平稳，适合趋势策略）。可有效过滤掉‘max_hold_timeout’和‘sl’类亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volexp",
            name="Volatility Explosion Detector",
            display_name="波动率爆发检测",
            description="通过比较当前短期波动率与历史波动率的比值，识别波动率突然放大的时刻。当短期波动率远高于近期平均水平时，因子值为负（表示高风险，不适合做多）；当波动率收缩时，因子值为正（表示市场平稳，适合趋势策略）。可有效过滤掉‘max_hold_timeout’和‘sl’类亏损。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算真实波幅(ATR近似)
        high_low = data['high'] - data['low']
        high_close = (data['high'] - data['close'].shift(1)).abs()
        low_close = (data['low'] - data['close'].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        # 短期波动率：5周期ATR均值
        short_vol = tr.rolling(5, min_periods=1).mean()
        # 长期波动率：50周期ATR均值
        long_vol = tr.rolling(50, min_periods=20).mean()
        # 波动率比值，避免除零
        ratio = short_vol / (long_vol + 1e-10)
        # 映射到[-1,1]，当比值>1.5时视为爆发，取负值
        result = 1 - 2 * (ratio > 1.5).astype(float) * (ratio - 1.5).clip(0, 2) / 2
        # 使用sigmoid平滑
        result = 2 / (1 + np.exp(-5 * (1.5 - ratio))) - 1
        # 移位避免未来
        result = result.shift(1)
        result = result.fillna(0)
        return result.clip(-1, 1)
