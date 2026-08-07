"""AI因子: 低波动率状态 | 置信:70% | 基于过去20个周期的平均真实波幅(ATR)与过去100个周期ATR中位数的比值。当比值低于0.5时，认为市场处于极低波动率状态，容易导致持仓超时亏损，因子输出负值；正常波动率输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LowVolatilityRegime(BaseFactor):
    """基于过去20个周期的平均真实波幅(ATR)与过去100个周期ATR中位数的比值。当比值低于0.5时，认为市场处于极低波动率状态，容易导致持仓超时亏损，因子输出负值；正常波动率输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_low",
            name="Low Volatility Regime",
            display_name="低波动率状态",
            description="基于过去20个周期的平均真实波幅(ATR)与过去100个周期ATR中位数的比值。当比值低于0.5时，认为市场处于极低波动率状态，容易导致持仓超时亏损，因子输出负值；正常波动率输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算ATR
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr_20 = tr.rolling(20).mean()
        atr_100_median = tr.rolling(100).median()
        ratio = atr_20 / atr_100_median
        # 映射到[-1,1]：低于0.5为-1，高于0.5线性映射到0~1，但保持敏感
        result = np.where(ratio < 0.5, -1.0, np.minimum(1.0, (ratio - 0.5) / 0.5))
        return pd.Series(result, index=data.index)
