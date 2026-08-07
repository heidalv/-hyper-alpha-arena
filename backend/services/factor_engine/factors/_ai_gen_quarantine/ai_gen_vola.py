"""AI因子: 波动率区间因子 | 置信:60% | 利用布林带宽度和价格在带中的位置识别高波动噪音环境。当波动率突然放大且价格处于区间中部时，容易产生假突破导致止损。因子输出负值表示危险状态，正值表示安全。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityZoneFactor(BaseFactor):
    """利用布林带宽度和价格在带中的位置识别高波动噪音环境。当波动率突然放大且价格处于区间中部时，容易产生假突破导致止损。因子输出负值表示危险状态，正值表示安全。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vola",
            name="Volatility Zone Factor",
            display_name="波动率区间因子",
            description="利用布林带宽度和价格在带中的位置识别高波动噪音环境。当波动率突然放大且价格处于区间中部时，容易产生假突破导致止损。因子输出负值表示危险状态，正值表示安全。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high']
        low = data['low']
        period = 20
        ma = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        bb_width = (upper - lower) / ma  # 相对宽度
        # 价格位置：0-1之间，0表示下轨，1表示上轨
        position = (close - lower) / (upper - lower)
        # 判断是否在高波动且位置在中间（0.3~0.7）
        vola_condition = (bb_width > bb_width.rolling(window=50).mean() * 1.5)
        middle_condition = (position > 0.3) & (position < 0.7)
        danger = vola_condition & middle_condition
        # 也考虑极端位置但波动放大时的反转风险？这里只取中间区域
        result = pd.Series(0.0, index=close.index)
        result[danger] = -1.0
        # 安全区：低波动且价格靠近均线？简单赋值为正
        safe = (bb_width < bb_width.rolling(window=50).mean() * 0.8) & (abs(position - 0.5) < 0.2)
        result[safe] = 1.0
        return result
