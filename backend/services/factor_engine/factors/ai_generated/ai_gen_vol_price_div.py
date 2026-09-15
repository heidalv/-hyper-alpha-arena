"""AI因子: 量价背离 | 置信:58% | 将短期价格变化方向与成交量变化方向做交互，衡量放量上涨/缩量下跌的一致性。量价同向时因子绝对值大且方向与价格一致，背离时反向，用于捕捉量价确认后的持续性收益。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """将短期价格变化方向与成交量变化方向做交互，衡量放量上涨/缩量下跌的一致性。量价同向时因子绝对值大且方向与价格一致，背离时反向，用于捕捉量价确认后的持续性收益。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="将短期价格变化方向与成交量变化方向做交互，衡量放量上涨/缩量下跌的一致性。量价同向时因子绝对值大且方向与价格一致，背离时反向，用于捕捉量价确认后的持续性收益。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_std = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = (ret * (vol_chg / vol_std)).clip(-1, 1)
        return result
