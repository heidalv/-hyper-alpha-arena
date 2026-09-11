"""AI因子: 插针密度均值回归 | 置信:55% | 插针密度 = max(upper,lower)/body 的20期滚动均值，衡量市场影线环境强度。高密度环境往往对应情绪化博弈与过度反应。将密度分位与短期收益反向交互，捕捉高插针密度下的均值回归：短期涨多则因子偏负，跌多则因子偏正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMeanReversion(BaseFactor):
    """插针密度 = max(upper,lower)/body 的20期滚动均值，衡量市场影线环境强度。高密度环境往往对应情绪化博弈与过度反应。将密度分位与短期收益反向交互，捕捉高插针密度下的均值回归：短期涨多则因子偏负，跌多则因子偏正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_rev",
            name="Pin Density Mean Reversion",
            display_name="插针密度均值回归",
            description="插针密度 = max(upper,lower)/body 的20期滚动均值，衡量市场影线环境强度。高密度环境往往对应情绪化博弈与过度反应。将密度分位与短期收益反向交互，捕捉高插针密度下的均值回归：短期涨多则因子偏负，跌多则因子偏正。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['open','close']].max(axis=1) - data['low'] + data['high'] - data[['open','close']].min(axis=1)) / body
        dens = density.rolling(20).mean()
        ret = data['close'].pct_change(5)
        result = (-ret * dens).rolling(5).mean().clip(-1, 1)
        return result
