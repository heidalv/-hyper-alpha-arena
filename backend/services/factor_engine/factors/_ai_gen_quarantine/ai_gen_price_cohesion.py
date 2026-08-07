"""AI因子: 价格凝聚度 | 置信:55% | 衡量价格在区间内的混乱程度。如果收盘价在高低点之间随机摆动，表明市场缺乏方向，容易触发止损或反向交易亏损。通过计算收盘位置在近期序列中的标准差来量化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceCohesion(BaseFactor):
    """衡量价格在区间内的混乱程度。如果收盘价在高低点之间随机摆动，表明市场缺乏方向，容易触发止损或反向交易亏损。通过计算收盘位置在近期序列中的标准差来量化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_cohesion",
            name="PriceCohesion",
            display_name="价格凝聚度",
            description="衡量价格在区间内的混乱程度。如果收盘价在高低点之间随机摆动，表明市场缺乏方向，容易触发止损或反向交易亏损。通过计算收盘位置在近期序列中的标准差来量化。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 日内位置: (close - low) / (high - low)，避免除以零
        high_low = data['high'] - data['low']
        pos = np.where(high_low > 1e-10, (data['close'] - data['low']) / high_low, 0.5)
        # 滚动20期标准差
        pos_std = pos.rolling(20).std()
        # 标准差范围通常在0~0.5之间，映射到[-1,1]：低标准差(凝聚)为正，高标准差(混乱)为负
        # 使用指数映射
        normalized = 1 - 2 * np.clip(pos_std / 0.5, 0, 1)
        return normalized
