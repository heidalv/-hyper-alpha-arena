"""AI因子: 波动率陷阱 | 置信:60% | 识别价格在窄幅盘整后出现异常波动但未能延续趋势，类似假突破。计算过去N根K线的ATR与当前K线真实波幅比值，同时结合当前收盘价与区间中轴的距离，当波幅骤增但价格偏离不显著时产生负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityTrap(BaseFactor):
    """识别价格在窄幅盘整后出现异常波动但未能延续趋势，类似假突破。计算过去N根K线的ATR与当前K线真实波幅比值，同时结合当前收盘价与区间中轴的距离，当波幅骤增但价格偏离不显著时产生负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_trap",
            name="Volatility Trap",
            display_name="波动率陷阱",
            description="识别价格在窄幅盘整后出现异常波动但未能延续趋势，类似假突破。计算过去N根K线的ATR与当前K线真实波幅比值，同时结合当前收盘价与区间中轴的距离，当波幅骤增但价格偏离不显著时产生负向信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR (14日)
        high, low, close = data['high'], data['low'], data['close']
        prev_close = close.shift(1)
        tr = np.maximum(high - low, np.abs(high - prev_close), np.abs(low - prev_close))
        atr = tr.rolling(14).mean()
        # 当前TR与ATR比值
        tr_ratio = tr / atr
        # 过去10日价格区间中轴
        mid_price = (high.rolling(10).max() + low.rolling(10).min()) / 2
        # 当前收盘偏离中轴程度（归一化到ATR）
        deviation = (close - mid_price) / atr
        # 当TR骤增（>2倍ATR）但偏离较小（|deviation|<1）时视为陷阱
        condition = (tr_ratio > 2.0) & (np.abs(deviation) < 1.0)
        # 映射到[-1,1]，触发则-1（不利），否则0
        result = np.where(condition, -1.0, 0.0)
        return pd.Series(result, index=data.index)
