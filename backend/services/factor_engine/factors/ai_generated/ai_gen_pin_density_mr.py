"""AI因子: 插针密度环境均值回归 | 置信:58% | 以(max(upper,lower)/body)的20期滚动均值刻画插针密度环境，高密度代表多空博弈剧烈、价格易过度偏离；用短期收益偏离度除以插针密度并反向，捕捉高插针环境下的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMeanReversion(BaseFactor):
    """以(max(upper,lower)/body)的20期滚动均值刻画插针密度环境，高密度代表多空博弈剧烈、价格易过度偏离；用短期收益偏离度除以插针密度并反向，捕捉高插针环境下的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mr",
            name="Pin Density Mean Reversion",
            display_name="插针密度环境均值回归",
            description="以(max(upper,lower)/body)的20期滚动均值刻画插针密度环境，高密度代表多空博弈剧烈、价格易过度偏离；用短期收益偏离度除以插针密度并反向，捕捉高插针环境下的均值回归alpha。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1) + 1e-9)
        pin = (upper + lower) / body
        denv = pin.rolling(20).mean()
        dev = data['close'] / data['close'].rolling(10).mean() - 1
        result = (-dev * denv).rolling(3).mean().clip(-1, 1)
        return result
