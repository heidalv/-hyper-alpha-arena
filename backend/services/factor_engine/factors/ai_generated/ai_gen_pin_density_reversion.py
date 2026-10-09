"""AI因子: 插针密度均值回归 | 置信:58% | 插针密度(影线/实体)的20期均值刻画波动环境，密度高时市场情绪化、均值回归强。用密度分位与短期收益反向交互，捕捉高插针环境下的反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMeanReversion(BaseFactor):
    """插针密度(影线/实体)的20期均值刻画波动环境，密度高时市场情绪化、均值回归强。用密度分位与短期收益反向交互，捕捉高插针环境下的反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_reversion",
            name="Pin Density Mean Reversion",
            display_name="插针密度均值回归",
            description="插针密度(影线/实体)的20期均值刻画波动环境，密度高时市场情绪化、均值回归强。用密度分位与短期收益反向交互，捕捉高插针环境下的反转alpha。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1))
        density = (data['high'] - data['low']) / body
        ret = data['close'].pct_change(3)
        result = (density.rolling(20).mean() * ret * -1.0).clip(-1, 1)
        return result
