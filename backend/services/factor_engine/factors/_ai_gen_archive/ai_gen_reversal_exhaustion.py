"""AI因子: 反转衰竭信号 | 置信:60% | 针对做空和做多均出现亏损的情况，捕捉价格在极端波动后的衰竭信号。使用收盘价相对最高最低价的位置，结合成交量萎缩，当价格处于区间高位且成交量萎缩时，做多衰竭（因子偏向+1）；当价格处于区间低位且成交量萎缩时，做空衰竭（因子偏向-1）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalExhaustion(BaseFactor):
    """针对做空和做多均出现亏损的情况，捕捉价格在极端波动后的衰竭信号。使用收盘价相对最高最低价的位置，结合成交量萎缩，当价格处于区间高位且成交量萎缩时，做多衰竭（因子偏向+1）；当价格处于区间低位且成交量萎缩时，做空衰竭（因子偏向-1）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_exhaustion",
            name="Reversal Exhaustion",
            display_name="反转衰竭信号",
            description="针对做空和做多均出现亏损的情况，捕捉价格在极端波动后的衰竭信号。使用收盘价相对最高最低价的位置，结合成交量萎缩，当价格处于区间高位且成交量萎缩时，做多衰竭（因子偏向+1）；当价格处于区间低位且成交量萎缩时，做空衰竭（因子偏向-1）。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_low_range = data['high'] - data['low']
        close_position = (data['close'] - data['low']) / (high_low_range + 1e-9)
        vol_ma = data['volume'].rolling(10).mean()
        vol_shrink = data['volume'] / (vol_ma + 1e-9)
        result = (close_position - 0.5) * 2 - (vol_shrink - 1.0)
        result = result.clip(-1, 1)
        return result
