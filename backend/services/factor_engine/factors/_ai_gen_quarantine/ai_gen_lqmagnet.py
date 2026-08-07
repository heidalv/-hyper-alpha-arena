"""AI因子: 流动性磁铁反转 | 置信:65% | 当价格接近近期最高价或最低价时，检测动量是否衰竭并可能反转。通过计算价格与近期极值的接近程度以及短期动量方向变化来量化反转风险。值接近+1表示强烈反转看空（价格在高位），-1表示强烈反转看多（价格在低位）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversal(BaseFactor):
    """当价格接近近期最高价或最低价时，检测动量是否衰竭并可能反转。通过计算价格与近期极值的接近程度以及短期动量方向变化来量化反转风险。值接近+1表示强烈反转看空（价格在高位），-1表示强烈反转看多（价格在低位）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lqmagnet",
            name="Liquidity Magnet Reversal",
            display_name="流动性磁铁反转",
            description="当价格接近近期最高价或最低价时，检测动量是否衰竭并可能反转。通过计算价格与近期极值的接近程度以及短期动量方向变化来量化反转风险。值接近+1表示强烈反转看空（价格在高位），-1表示强烈反转看多（价格在低位）。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算近期最高价和最低价（20周期）
        high20 = data['close'].rolling(20).max()
        low20 = data['close'].rolling(20).min()
        # 价格相对于极值的位置
        near_high = (data['close'] / high20) > 0.98
        near_low = (data['close'] / low20) < 1.02
        # 短期动量（3周期变化率）
        mom3 = data['close'].pct_change(3)
        # 动量方向反转信号：接近高点且动量转负，或接近低点且动量转正
        rev_signal = pd.Series(0.0, index=data.index)
        rev_signal[near_high & (mom3 < -0.005)] = 1.0
        rev_signal[near_low & (mom3 > 0.005)] = -1.0
        return rev_signal
