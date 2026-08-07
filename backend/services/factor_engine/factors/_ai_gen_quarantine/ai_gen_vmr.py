"""AI因子: 波动率调整动量比 | 置信:70% | 将短期动量除以波动率，信号放大但过滤低波动混沌行情。当比值接近0时表示无明确趋势（regime unknown），输出[-1,+1]：+1为强势上涨，-1为强势下跌，0附近为震荡。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Momentum_Ratio(BaseFactor):
    """将短期动量除以波动率，信号放大但过滤低波动混沌行情。当比值接近0时表示无明确趋势（regime unknown），输出[-1,+1]：+1为强势上涨，-1为强势下跌，0附近为震荡。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vmr",
            name="Volatility-Momentum Ratio",
            display_name="波动率调整动量比",
            description="将短期动量除以波动率，信号放大但过滤低波动混沌行情。当比值接近0时表示无明确趋势（regime unknown），输出[-1,+1]：+1为强势上涨，-1为强势下跌，0附近为震荡。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        short_period = 5
        vol_period = 20
        # 动量     ret = data['close'].pct_change(short_period)
        # 波动率（年化简化为滚动标准差）     daily_ret = data['close'].pct_change()
        vol = daily_ret.rolling(vol_period).std() * np.sqrt(252)  # 年化波动率
        # 避免除以0     vol = vol.replace(0, np.nan)
        ratio = ret / vol
        # 缩放到[-1,1]，使用z-score然后tanh
        mean = ratio.rolling(100).mean()
        std = ratio.rolling(100).std()
        z = (ratio - mean) / (std + 1e-8)
        z = z.clip(-3, 3)
        # 用tanh映射到[-1,1]
        result = np.tanh(z)
        return result.fillna(0)
