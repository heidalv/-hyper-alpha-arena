"""AI因子: 布林带带宽收缩 | 置信:55% | 衡量布林带带宽的相对变化，带宽收缩通常预示突破，但在突破方向不明时趋势策略容易亏损。因子为负值表示带宽收缩且价格处于中间区域，提示风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerBandWidthContraction(BaseFactor):
    """衡量布林带带宽的相对变化，带宽收缩通常预示突破，但在突破方向不明时趋势策略容易亏损。因子为负值表示带宽收缩且价格处于中间区域，提示风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bbw",
            name="Bollinger Band Width Contraction",
            display_name="布林带带宽收缩",
            description="衡量布林带带宽的相对变化，带宽收缩通常预示突破，但在突破方向不明时趋势策略容易亏损。因子为负值表示带宽收缩且价格处于中间区域，提示风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        period = 20
        std_mult = 2
        close = df['close']
        high = df['high']
        low = df['low']
        # 计算布林带
        sma = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()
        upper = sma + std_mult * std
        lower = sma - std_mult * std
        bandwidth = (upper - lower) / sma
        # 带宽变化率: 当前带宽与过去20日平均带宽的比率
        avg_bandwidth = bandwidth.rolling(window=period).mean()
        bw_ratio = bandwidth / avg_bandwidth - 1  # 正表示扩张，负表示收缩
        # 价格位置: (close - lower) / (upper - lower) 归一化到0-1
        position = (close - lower) / (upper - lower).replace(0, np.nan)
        # 当带宽收缩且价格在中间(0.3~0.7)时，因子为负，否则为正
        mid = ((position > 0.3) & (position < 0.7)).astype(float)
        factor = -bw_ratio * mid
        # 平滑并缩放到[-1,1]
        factor = factor.rolling(window=3).mean()
        factor = factor.clip(-1, 1)
        return factor
