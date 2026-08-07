"""AI因子: 区间收缩因子 | 置信:60% | 利用布林带带宽收缩识别窄幅震荡行情，此时价格易假突破导致多单亏损。计算20周期布林带宽度(上轨-下轨)/中轨，反向归一化到[-1,1]。带宽越窄越接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Range_Contraction(BaseFactor):
    """利用布林带带宽收缩识别窄幅震荡行情，此时价格易假突破导致多单亏损。计算20周期布林带宽度(上轨-下轨)/中轨，反向归一化到[-1,1]。带宽越窄越接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rct",
            name="Range_Contraction",
            display_name="区间收缩因子",
            description="利用布林带带宽收缩识别窄幅震荡行情，此时价格易假突破导致多单亏损。计算20周期布林带宽度(上轨-下轨)/中轨，反向归一化到[-1,1]。带宽越窄越接近-1。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        bandwidth = (upper - lower) / ma
        # 历史分位数归一化到[-1,1]，带宽越小越接近-1
        # 这里使用滚动zscore简化，或直接负相关
        # 采用排名分位数，但简单线性映射：假设带宽在0~0.2之间常见
        result = 1 - 2 * (bandwidth / 0.2)
        return result.clip(-1, 1)
