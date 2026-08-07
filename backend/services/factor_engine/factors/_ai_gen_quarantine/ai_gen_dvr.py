"""AI因子: 方向波动比率 | 置信:65% | 比较上涨波动率与下跌波动率，二者均衡时市场无方向，易出现持仓超时亏损。正值表示上涨波动主导，负值表示下跌波动主导，近0表示无方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DirectionalVolatilityRatio(BaseFactor):
    """比较上涨波动率与下跌波动率，二者均衡时市场无方向，易出现持仓超时亏损。正值表示上涨波动主导，负值表示下跌波动主导，近0表示无方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dvr",
            name="Directional Volatility Ratio",
            display_name="方向波动比率",
            description="比较上涨波动率与下跌波动率，二者均衡时市场无方向，易出现持仓超时亏损。正值表示上涨波动主导，负值表示下跌波动主导，近0表示无方向。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        N = 20
        eps = 1e-8
        ret = close.diff()
        up_vol = ret.clip(lower=0).rolling(N).std()
        down_vol = ret.clip(upper=0).abs().rolling(N).std()
        ratio = (up_vol - down_vol) / (up_vol + down_vol + eps)
        return ratio.fillna(0).clip(-1, 1)
