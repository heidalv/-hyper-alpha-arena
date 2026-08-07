"""AI因子: 微幅波动频率 | 置信:60% | 计算最近N根K线中，价格振幅（(high-low)/close）小于阈值（0.002）的比例，衡量市场微幅盘整程度。高频微幅波动易导致close_tiny类亏损。归一化至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Micro_Range_Frequency(BaseFactor):
    """计算最近N根K线中，价格振幅（(high-low)/close）小于阈值（0.002）的比例，衡量市场微幅盘整程度。高频微幅波动易导致close_tiny类亏损。归一化至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_microrange",
            name="Micro_Range_Frequency",
            display_name="微幅波动频率",
            description="计算最近N根K线中，价格振幅（(high-low)/close）小于阈值（0.002）的比例，衡量市场微幅盘整程度。高频微幅波动易导致close_tiny类亏损。归一化至[-1,1]。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 20
        thr = 0.002
        amplitude = (data['high'] - data['low']) / data['close']
        tiny = (amplitude < thr).astype(float)
        ratio = tiny.rolling(window=n, min_periods=1).mean()
        # 归一化到[-1,1]：假设ratio在0~1，映射到-1~1
        return 2 * ratio - 1
