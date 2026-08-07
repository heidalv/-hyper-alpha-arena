"""AI因子: 未知状态K线形态 | 置信:60% | 通过K线实体的相对大小和影线长度判断市场犹豫程度。当实体很小（收盘接近开盘）且上下影线较长时，表明多空分歧大，市场处于未知状态；当实体很大（如长阳或长阴）时，趋势明确。因子值根据实体占比和影线长度计算，范围[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeCandleShape(BaseFactor):
    """通过K线实体的相对大小和影线长度判断市场犹豫程度。当实体很小（收盘接近开盘）且上下影线较长时，表明多空分歧大，市场处于未知状态；当实体很大（如长阳或长阴）时，趋势明确。因子值根据实体占比和影线长度计算，范围[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unk_shape",
            name="UnknownRegimeCandleShape",
            display_name="未知状态K线形态",
            description="通过K线实体的相对大小和影线长度判断市场犹豫程度。当实体很小（收盘接近开盘）且上下影线较长时，表明多空分歧大，市场处于未知状态；当实体很大（如长阳或长阴）时，趋势明确。因子值根据实体占比和影线长度计算，范围[-1,1]。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        open_price = data['open']
        close = data['close']
        high = data['high']
        low = data['low']
        # 实体大小
        body = np.abs(close - open_price)
        # 上下影线
        upper_shadow = high - np.maximum(open_price, close)
        lower_shadow = np.minimum(open_price, close) - low
        # 总振幅
        total_range = high - low
        # 避免除以0
        total_range = np.maximum(total_range, 1e-10)
        # 实体占比（越小越犹豫），影线占比（越大越犹豫）
        body_ratio = body / total_range
        shadow_ratio = (upper_shadow + lower_shadow) / total_range
        # 犹豫指数 = (1 - body_ratio) * shadow_ratio，取值范围[0,1]
        hesitation = (1 - body_ratio) * shadow_ratio
        # 映射到[-1,1]：犹豫指数0.5为中心，低于0.5（趋势明显）映射到正，高于0.5映射到负
        score = 1 - 2 * hesitation
        # 平滑处理
        result = score
        return result
