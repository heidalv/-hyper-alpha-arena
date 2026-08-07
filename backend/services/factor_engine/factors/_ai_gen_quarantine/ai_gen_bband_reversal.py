"""AI因子: 布林带反转风险 | 置信:60% | 当价格突破布林带上轨且成交量显著放大时，预示假突破风险，做多易亏损。因子值接近+1表示高风险，接近-1表示安全。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Band_Reversal_Risk(BaseFactor):
    """当价格突破布林带上轨且成交量显著放大时，预示假突破风险，做多易亏损。因子值接近+1表示高风险，接近-1表示安全。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bband_reversal",
            name="Bollinger Band Reversal Risk",
            display_name="布林带反转风险",
            description="当价格突破布林带上轨且成交量显著放大时，预示假突破风险，做多易亏损。因子值接近+1表示高风险，接近-1表示安全。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 布林带参数
        window = 20
        std_mult = 2.0
        sma = close.rolling(window).mean()
        std = close.rolling(window).std()
        upper = sma + std_mult * std
        lower = sma - std_mult * std
        # 成交量均线
        vol_ma = volume.rolling(window).mean()
        # 信号：突破上轨且成交量放大1.5倍 -> 做多风险高
        risky = (close > upper) & (volume > 1.5 * vol_ma)
        # 突破下轨且成交量放大 -> 做多风险低（可能反弹）
        safe = (close < lower) & (volume > 1.5 * vol_ma)
        # 映射到[-1,1]
        result = pd.Series(np.zeros(len(close)), index=close.index)
        result[risky] = 1.0
        result[safe] = -1.0
        return result
