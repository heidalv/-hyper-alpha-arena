"""AI因子: 波动稳定性 | 置信:65% | 衡量近期波动率的变化程度，波动率急剧放大或收窄时市场状态未知，输出接近0；波动率稳定时根据趋势方向输出。使用标准差比值，通过tanh映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityStability(BaseFactor):
    """衡量近期波动率的变化程度，波动率急剧放大或收窄时市场状态未知，输出接近0；波动率稳定时根据趋势方向输出。使用标准差比值，通过tanh映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volstab",
            name="Volatility Stability",
            display_name="波动稳定性",
            description="衡量近期波动率的变化程度，波动率急剧放大或收窄时市场状态未知，输出接近0；波动率稳定时根据趋势方向输出。使用标准差比值，通过tanh映射到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算对数收益率的滚动波动率
        returns = np.log(data['close'] / data['close'].shift(1))
        vol_short = returns.rolling(10).std()
        vol_long = returns.rolling(30).std()
        # 波动率比值，反映稳定性
        ratio = vol_short / vol_long.replace(0, np.nan)
        # 用tanh映射到[-1,1]，当ratio接近1时稳定，输出接近1；偏离时输出接近0
        stability = 1 - np.abs(ratio - 1)  # 0到1之间
        # 结合方向动量
        momentum = np.sign(data['close'] - data['close'].shift(10))
        result = stability * momentum
        result = result.fillna(0).clip(-1, 1)
        return result
