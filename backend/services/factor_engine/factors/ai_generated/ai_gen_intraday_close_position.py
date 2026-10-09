"""AI因子: 日内收盘位置动量 | 置信:60% | 收盘价在当日高低区间中的相对位置反映多空力量对比：收盘接近高点表明买方强势，接近低点表明卖方强势。结合短期动量方向，捕捉趋势延续或反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class IntradayClosePositionMomentum(BaseFactor):
    """收盘价在当日高低区间中的相对位置反映多空力量对比：收盘接近高点表明买方强势，接近低点表明卖方强势。结合短期动量方向，捕捉趋势延续或反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_intraday_close_position",
            name="Intraday Close Position Momentum",
            display_name="日内收盘位置动量",
            description="收盘价在当日高低区间中的相对位置反映多空力量对比：收盘接近高点表明买方强势，接近低点表明卖方强势。结合短期动量方向，捕捉趋势延续或反转。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = data['high'] - data['low'] + 1e-9
        pos = (data['close'] - data['low']) / rng
        mom = data['close'].pct_change(3)
        result = ((pos - 0.5) * 2 * mom.rolling(3).mean().apply(lambda x: 1 if x > 0 else -1)).clip(-1, 1)
        return result
