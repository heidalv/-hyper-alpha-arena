"""AI因子: 下影线承接反转因子 | 置信:62% | 长下影线代表买方在低位防守承接，叠加短期趋势方向过滤：当下影主导且价格处于短期弱势时，反弹概率更高。用影线不对称度与短期收益方向交互构造。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LowerShadowReversalWithTrendFilter(BaseFactor):
    """长下影线代表买方在低位防守承接，叠加短期趋势方向过滤：当下影主导且价格处于短期弱势时，反弹概率更高。用影线不对称度与短期收益方向交互构造。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_lower_shadow_rev",
            name="Lower Shadow Reversal with Trend Filter",
            display_name="下影线承接反转因子",
            description="长下影线代表买方在低位防守承接，叠加短期趋势方向过滤：当下影主导且价格处于短期弱势时，反弹概率更高。用影线不对称度与短期收益方向交互构造。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        trend = data['close'].pct_change(5)
        result = (asym.rolling(3).mean() * (-trend).clip(-1, 1)).rolling(5).mean().clip(-1, 1)
        return result
