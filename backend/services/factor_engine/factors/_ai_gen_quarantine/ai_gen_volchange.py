"""AI因子: 波动率变化率 | 置信:70% | 计算过去N根K线的波动率（ATR/价格）的短周期变化率，当波动率突然放大且方向不明时，预示regime unknown状态。通过比较近期波动率与长期均值，输出值域[-1,1]，正值表示风险上升。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Rate_of_Change(BaseFactor):
    """计算过去N根K线的波动率（ATR/价格）的短周期变化率，当波动率突然放大且方向不明时，预示regime unknown状态。通过比较近期波动率与长期均值，输出值域[-1,1]，正值表示风险上升。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volchange",
            name="Volatility_Rate_of_Change",
            display_name="波动率变化率",
            description="计算过去N根K线的波动率（ATR/价格）的短周期变化率，当波动率突然放大且方向不明时，预示regime unknown状态。通过比较近期波动率与长期均值，输出值域[-1,1]，正值表示风险上升。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(7).mean()
        # 波动率归一化：ATR/收盘价
        vol = atr / data['close']
        vol_short = vol.rolling(3).mean()
        vol_long = vol.rolling(20).mean()
        # 变化率
        ratio = (vol_short - vol_long) / vol_long.clip(lower=1e-8)
        # 映射到[-1,1]，限制在3倍标准差内
        mean = ratio.rolling(30).mean()
        std = ratio.rolling(30).std().clip(lower=1e-8)
        zscore = (ratio - mean) / std
        result = np.clip(zscore, -3, 3) / 3
        return result.ffill().fillna(0)
