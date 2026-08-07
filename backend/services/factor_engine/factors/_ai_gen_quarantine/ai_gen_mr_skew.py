"""AI因子: 均值回归偏度 | 置信:60% | 基于价格与短期均线的偏离程度，结合成交量确认，当价格过度偏离且成交量放大时，预测反转。适用于捕捉类似liq_magnet_reversal和ai_reverse的反转亏损模式。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionSkew(BaseFactor):
    """基于价格与短期均线的偏离程度，结合成交量确认，当价格过度偏离且成交量放大时，预测反转。适用于捕捉类似liq_magnet_reversal和ai_reverse的反转亏损模式。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mr_skew",
            name="Mean Reversion Skew",
            display_name="均值回归偏度",
            description="基于价格与短期均线的偏离程度，结合成交量确认，当价格过度偏离且成交量放大时，预测反转。适用于捕捉类似liq_magnet_reversal和ai_reverse的反转亏损模式。",
            category="mean_reversion",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        # 偏离度: (close - ma5)/ma5
        dev = (close - ma5) / ma5
        # 成交量相对均值
        vol_ratio = volume / volume.rolling(20).mean()
        # 信号: 当价格偏离大于2%且成交量放大>1.5倍时，预期反转
        signal = np.where((dev > 0.02) & (vol_ratio > 1.5), -1, 
                          np.where((dev < -0.02) & (vol_ratio > 1.5), 1, 0))
        # 平滑为series
        result = pd.Series(signal, index=close.index)
        return result
