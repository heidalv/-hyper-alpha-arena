"""AI因子: 趋势明确度 | 置信:50% | 基于3个不同周期（5,10,20）EMA的排列方向一致性，判断当前是否存在明确趋势。当均线多头/空头排列一致时因子值接近+1（趋势强），混乱时接近-1（震荡，易止损）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendDirectionality(BaseFactor):
    """基于3个不同周期（5,10,20）EMA的排列方向一致性，判断当前是否存在明确趋势。当均线多头/空头排列一致时因子值接近+1（趋势强），混乱时接近-1（震荡，易止损）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trd",
            name="Trend Directionality",
            display_name="趋势明确度",
            description="基于3个不同周期（5,10,20）EMA的排列方向一致性，判断当前是否存在明确趋势。当均线多头/空头排列一致时因子值接近+1（趋势强），混乱时接近-1（震荡，易止损）。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        ema5 = close.ewm(span=5, adjust=False).mean()
        ema10 = close.ewm(span=10, adjust=False).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        # 判断均线方向：当前值相对上一周期的变化
        def direction(series):
            return (series.diff() > 0).astype(int)
        d5 = direction(ema5)
        d10 = direction(ema10)
        d20 = direction(ema20)
        # 一致性：三个方向之和为0或3时一致，否则混乱
        consistency = (d5 + d10 + d20)  # 0,1,2,3
        # 映射：0->-1（全都向下），3->+1（全都向上），1,2-> 根据多数方向？更简单：用(consistency-1.5)/1.5缩放到[-1,1]
        result = (consistency - 1.5) / 1.5
        return result.fillna(0)
