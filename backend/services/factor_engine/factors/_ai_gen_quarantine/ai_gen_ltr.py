"""AI因子: 多头陷阱因子 | 置信:65% | 捕捉日内冲高回落且成交量萎缩的形态，此类场景容易引发多单止损。计算当前收盘价相对于当日最高点的回落比例，并结合成交量变化，输出负值表示多头陷阱风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Long_Trap(BaseFactor):
    """捕捉日内冲高回落且成交量萎缩的形态，此类场景容易引发多单止损。计算当前收盘价相对于当日最高点的回落比例，并结合成交量变化，输出负值表示多头陷阱风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ltr",
            name="Long_Trap",
            display_name="多头陷阱因子",
            description="捕捉日内冲高回落且成交量萎缩的形态，此类场景容易引发多单止损。计算当前收盘价相对于当日最高点的回落比例，并结合成交量变化，输出负值表示多头陷阱风险高。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close, high, low, volume = data['close'], data['high'], data['low'], data['volume']
        # 日内回落幅度: (high - close) / (high - low + 1e-10)
        daily_range = high - low
        pullback_ratio = (high - close) / (daily_range + 1e-10)
        # 成交量变化：今日成交量相对前5日均值的比值
        avg_vol = volume.rolling(5).mean()
        vol_ratio = volume / (avg_vol + 1e-10)
        # 多头陷阱：回落大且成交量萎缩（vol_ratio<1）
        trap_score = pullback_ratio * (1 - vol_ratio.clip(0,2))
        # 归一化到[-1,1]，使用tanh控制范围
        result = np.tanh(trap_score * 5)
        return -result  # 负值表示危险
