"""AI因子: 反转量能因子 | 置信:50% | 基于价格极值后的反转力量与成交量放大程度，当价格突破近期极值但成交量未能持续放大时，预示反转；反之，若成交量跟随突破则趋势延续。输出正值表示反转概率高（做空信号），负值表示趋势延续（做多信号）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversal_Volume_Power(BaseFactor):
    """基于价格极值后的反转力量与成交量放大程度，当价格突破近期极值但成交量未能持续放大时，预示反转；反之，若成交量跟随突破则趋势延续。输出正值表示反转概率高（做空信号），负值表示趋势延续（做多信号）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rvp",
            name="Reversal_Volume_Power",
            display_name="反转量能因子",
            description="基于价格极值后的反转力量与成交量放大程度，当价格突破近期极值但成交量未能持续放大时，预示反转；反之，若成交量跟随突破则趋势延续。输出正值表示反转概率高（做空信号），负值表示趋势延续（做多信号）。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']

        # 计算近期最高和最低（20周期）
        rolling_high = high.rolling(20).max()
        rolling_low = low.rolling(20).min()

        # 判断是否突破近期极值
        new_high = close > rolling_high.shift(1)
        new_low = close < rolling_low.shift(1)

        # 计算价格变化幅度
        price_change = close.pct_change()

        # 计算成交量变化率
        volume_change = volume.pct_change()

        # 反转力量：突破极值但成交量萎缩或价格动量不足
        # 做空信号条件：创新高但成交量下降或价格涨幅小
        short_signal = new_high & ((volume_change < 0) | (price_change < price_change.rolling(10).mean()))
        # 做多信号条件：创新低但成交量下降或价格跌幅小
        long_signal = new_low & ((volume_change < 0) | (price_change > price_change.rolling(10).mean()))

        # 归一化到[-1,1]，正值为做空信号，负值为做多信号
        signal = pd.Series(0.0, index=close.index)
        signal[short_signal] = 1.0
        signal[long_signal] = -1.0
        return signal
