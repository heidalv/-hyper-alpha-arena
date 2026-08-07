"""AI因子: 价格位置阻力因子 | 置信:75% | 基于近期价格通道中的相对位置，判断当前价格是否处于极端区域（高位或低位）。在未知状态下，高位追涨和低位杀跌容易亏损。因子在价格适中时为正，极端时为负。使用布林带百分比或分位数。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PricePosition(BaseFactor):
    """基于近期价格通道中的相对位置，判断当前价格是否处于极端区域（高位或低位）。在未知状态下，高位追涨和低位杀跌容易亏损。因子在价格适中时为正，极端时为负。使用布林带百分比或分位数。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_position",
            name="PricePosition",
            display_name="价格位置阻力因子",
            description="基于近期价格通道中的相对位置，判断当前价格是否处于极端区域（高位或低位）。在未知状态下，高位追涨和低位杀跌容易亏损。因子在价格适中时为正，极端时为负。使用布林带百分比或分位数。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 使用20日布林带
        mid = data['close'].rolling(20).mean()
        std = data['close'].rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        # 计算价格在布林带中的位置，0~1
        position = (data['close'] - lower) / (upper - lower + 1e-10)
        # 将[0,1]映射到[-1,1]：中间0.5->0，极端0或1->-1
        result = -np.abs(position - 0.5) * 2 + 0  # 线性：0->-1, 0.5->1, 1->-1
        # 实际映射：position在0.2~0.8时为正，否则为负
        # 使用对称变换：1 - 2*|position-0.5|
        result = 1 - 2 * np.abs(position - 0.5)
        result = result.fillna(method='ffill').fillna(0)
        return result
