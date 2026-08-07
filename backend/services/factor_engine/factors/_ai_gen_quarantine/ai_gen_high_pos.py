"""AI因子: 高位反向 | 置信:60% | 计算当前收盘价相对于过去20天最高最低的位置，当价格处于高位（接近近期高点）时，表明可能追高被套，适合做空信号，输出负值。使用归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class High_Position_Contrarian(BaseFactor):
    """计算当前收盘价相对于过去20天最高最低的位置，当价格处于高位（接近近期高点）时，表明可能追高被套，适合做空信号，输出负值。使用归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_high_pos",
            name="High Position Contrarian",
            display_name="高位反向",
            description="计算当前收盘价相对于过去20天最高最低的位置，当价格处于高位（接近近期高点）时，表明可能追高被套，适合做空信号，输出负值。使用归一化到[-1,1]。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        window = 20
        high_20 = data['high'].rolling(window).max()
        low_20 = data['low'].rolling(window).min()
        range_ = high_20 - low_20
        # 避免除以0
        range_ = range_.replace(0, np.nan)
        pos = (data['close'] - low_20) / range_
        # pos 在0-1之间，映射到[-1,1]：1-2*pos，使得高点时负，低点时正
        result = 1 - 2 * pos
        # 向前填充NaN，避免前几个值缺失
        result = result.ffill()
        return result
