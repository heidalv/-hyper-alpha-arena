"""AI因子: 成交量振荡器位置 | 置信:60% | 价格在布林带中的相对位置与成交量变化率的组合。当价格位于中轨附近且成交量萎缩时，市场缺乏方向，易出现类似亏损模式的假突破。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Oscillator_Position(BaseFactor):
    """价格在布林带中的相对位置与成交量变化率的组合。当价格位于中轨附近且成交量萎缩时，市场缺乏方向，易出现类似亏损模式的假突破。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vop",
            name="Volume Oscillator Position",
            display_name="成交量振荡器位置",
            description="价格在布林带中的相对位置与成交量变化率的组合。当价格位于中轨附近且成交量萎缩时，市场缺乏方向，易出现类似亏损模式的假突破。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 布林带 (20,2)
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        # 价格在布林带中的位置 [-1,1]，0表示中轨
        pos = 2 * (close - ma) / (upper - lower + 1e-10)
        # 成交量变化率 (5日平均相对20日平均)
        vol_ma_short = volume.rolling(5).mean()
        vol_ma_long = volume.rolling(20).mean()
        vol_ratio = vol_ma_short / (vol_ma_long + 1e-10) - 1  # 正表示放量，负缩量
        # 组合：位置接近0且缩量 -> 负值
        # 用高斯核衡量位置接近0的程度
        proximity = np.exp(-4 * pos**2)  # 在0附近为1，远为0
        # 缩量因子：vol_ratio为负时，取绝对值
        shrink = np.maximum(0, -vol_ratio)  # 0~1
        # 综合信号：接近中轨且缩量 => 负值，其他情况偏向正值
        combined = 0.5 * (1 - proximity) + 0.5 * (1 - shrink)
        result = 2 * combined - 1  # 映射到[-1,1]
        return result.fillna(0).clip(-1, 1)
