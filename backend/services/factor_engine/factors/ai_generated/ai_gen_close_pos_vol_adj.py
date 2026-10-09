"""AI因子: 波动调整收盘位置 | 置信:58% | 收盘价在近20日高低区间中的相对位置，衡量价格在近期区间的强弱。结合波动率环境，位置高且波动收敛时上涨概率更高，位置低时超卖反弹。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedClosePosition(BaseFactor):
    """收盘价在近20日高低区间中的相对位置，衡量价格在近期区间的强弱。结合波动率环境，位置高且波动收敛时上涨概率更高，位置低时超卖反弹。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_pos_vol_adj",
            name="Volatility-Adjusted Close Position",
            display_name="波动调整收盘位置",
            description="收盘价在近20日高低区间中的相对位置，衡量价格在近期区间的强弱。结合波动率环境，位置高且波动收敛时上涨概率更高，位置低时超卖反弹。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        hi = data['high'].rolling(20).max()
        lo = data['low'].rolling(20).min()
        pos = (data['close'] - lo) / (hi - lo + 1e-9)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((pos - 0.5) * 2 / (1 + vol * 20)).clip(-1, 1)
        return result
