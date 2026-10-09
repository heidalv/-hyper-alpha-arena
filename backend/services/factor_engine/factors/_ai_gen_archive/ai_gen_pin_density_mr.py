"""AI因子: 插针密度均值回归 | 置信:58% | 以影线相对实体的比值衡量插针密度，密度高说明市场处于多空拉锯的震荡环境，短期收益容易均值回归。将插针密度与短期收益方向交互，密度越高时反转信号越强。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMeanReversion(BaseFactor):
    """以影线相对实体的比值衡量插针密度，密度高说明市场处于多空拉锯的震荡环境，短期收益容易均值回归。将插针密度与短期收益方向交互，密度越高时反转信号越强。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mr",
            name="Pin Density Mean Reversion",
            display_name="插针密度均值回归",
            description="以影线相对实体的比值衡量插针密度，密度高说明市场处于多空拉锯的震荡环境，短期收益容易均值回归。将插针密度与短期收益方向交互，密度越高时反转信号越强。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin = (upper - lower) / body
        density = (pin.abs()).rolling(20).mean()
        ret = data['close'].pct_change(5)
        result = (-ret * density).clip(-1, 1)
        return result
