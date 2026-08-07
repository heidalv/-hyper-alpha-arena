"""AI因子: 区间突破真实性 | 置信:60% | 基于布林带宽度与价格在带中位置，判断当前价格是否处于真实突破还是假突破。当价格突破带宽但带宽过窄或成交量不足时，视为假突破（负值），适合做多时正值。用于避免resolv/layer等假突破止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Range_Break_Authenticity(BaseFactor):
    """基于布林带宽度与价格在带中位置，判断当前价格是否处于真实突破还是假突破。当价格突破带宽但带宽过窄或成交量不足时，视为假突破（负值），适合做多时正值。用于避免resolv/layer等假突破止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_range_break",
            name="Range_Break_Authenticity",
            display_name="区间突破真实性",
            description="基于布林带宽度与价格在带中位置，判断当前价格是否处于真实突破还是假突破。当价格突破带宽但带宽过窄或成交量不足时，视为假突破（负值），适合做多时正值。用于避免resolv/layer等假突破止损。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        vol = data['volume']
        # 布林带
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        bandwidth = (upper - lower) / (ma + 1e-8)
        # 价格位置
        pos = (close - ma) / (std + 1e-8)
        # 成交量变化率
        vol_ratio = vol / vol.rolling(20).mean()
        # 因子：突破时带宽适度、成交量放大为正，否则为负
        # 当pos>2或pos<-2时，可能突破，但需要带宽>0.1且vol_ratio>1.2才积极
        result = np.where(
            (np.abs(pos) > 2) & (bandwidth > 0.1) & (vol_ratio > 1.2),
            np.sign(pos) * 0.8,
            np.where(
                np.abs(pos) < 1.5,
                -0.3,
                np.tanh(pos * 0.2)
            )
        )
        return result
