"""AI因子: 低量窄幅震荡因子 | 置信:60% | 识别价格在窄幅区间内波动且成交量萎缩的市场状态，此类状态容易导致假突破和亏损（regime=unknown）。通过计算价格通道宽度与成交量相对均值的比值，值越高表示震荡越明显，映射到负值区域"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Low_Volume_Consolidation(BaseFactor):
    """识别价格在窄幅区间内波动且成交量萎缩的市场状态，此类状态容易导致假突破和亏损（regime=unknown）。通过计算价格通道宽度与成交量相对均值的比值，值越高表示震荡越明显，映射到负值区域"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ltvol",
            name="Low_Volume_Consolidation",
            display_name="低量窄幅震荡因子",
            description="识别价格在窄幅区间内波动且成交量萎缩的市场状态，此类状态容易导致假突破和亏损（regime=unknown）。通过计算价格通道宽度与成交量相对均值的比值，值越高表示震荡越明显，映射到负值区域",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        volume = data['volume']
        window = 20
        # 价格通道宽度 (最高最高 - 最低最低) / 中价
        rolling_high = high.rolling(window).max()
        rolling_low = low.rolling(window).min()
        mid_price = (high + low) / 2
        channel_width = (rolling_high - rolling_low) / mid_price
        # 成交量相对均值
        vol_ma = volume.rolling(window).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 组合：通道收窄且成交量低 -> 值接近1（映射到-1）
        # 使用 (1 - channel_width) * (1 - vol_ratio) 然后归一化
        raw = (1 - channel_width) * (1 - vol_ratio)
        # 将raw限制在[0,1]区间并映射到[-1,0]（低量窄幅震荡为负值）
        raw_clipped = np.clip(raw, 0, 1)
        result = -raw_clipped
        return result
