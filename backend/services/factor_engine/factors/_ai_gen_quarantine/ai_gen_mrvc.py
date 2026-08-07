"""AI因子: 波动率汇聚均值回复 | 置信:65% | 当价格短期波动率（1小时ATR）相对于长期波动率（24小时ATR）明显偏低，且价格偏离短期均线超过1个标准差时，预期价格回归均线。结合成交量萎缩确认震荡状态，适用于regime=unknown的震荡行情。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionVolatilityConfluence(BaseFactor):
    """当价格短期波动率（1小时ATR）相对于长期波动率（24小时ATR）明显偏低，且价格偏离短期均线超过1个标准差时，预期价格回归均线。结合成交量萎缩确认震荡状态，适用于regime=unknown的震荡行情。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrvc",
            name="MeanReversionVolatilityConfluence",
            display_name="波动率汇聚均值回复",
            description="当价格短期波动率（1小时ATR）相对于长期波动率（24小时ATR）明显偏低，且价格偏离短期均线超过1个标准差时，预期价格回归均线。结合成交量萎缩确认震荡状态，适用于regime=unknown的震荡行情。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr_short = tr.rolling(6).mean()  # 约1小时（假设15分钟K线）
        atr_long = tr.rolling(96).mean()  # 约24小时
        atr_ratio = atr_short / atr_long

        # 价格偏离短期均线
        ma_short = close.rolling(12).mean()
        std_short = close.rolling(12).std()
        zscore = (close - ma_short) / (std_short + 1e-9)

        # 成交量萎缩条件
        volume = data['volume']
        vol_ma = volume.rolling(12).mean()
        vol_ratio = volume / (vol_ma + 1e-9)

        # 组合：低波动率汇聚 + 价格偏离均线 + 成交量萎缩 => 做反向
        signal = np.where((atr_ratio < 0.5) & (np.abs(zscore) > 2.0) & (vol_ratio < 0.8), -np.sign(zscore), 0)
        return pd.Series(signal, index=data.index).clip(-1, 1)
