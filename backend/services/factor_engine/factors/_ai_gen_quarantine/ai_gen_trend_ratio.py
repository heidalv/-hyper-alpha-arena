"""AI因子: 趋势噪声比 | 置信:50% | 计算过去N周期的价格净变动绝对值与ATR的比值，衡量趋势强度相对于噪音的大小。比值高表示趋势强劲（因子+1），比值低表示震荡或unsure状态（因子-1）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendToNoiseRatio(BaseFactor):
    """计算过去N周期的价格净变动绝对值与ATR的比值，衡量趋势强度相对于噪音的大小。比值高表示趋势强劲（因子+1），比值低表示震荡或unsure状态（因子-1）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_ratio",
            name="Trend-to-Noise Ratio",
            display_name="趋势噪声比",
            description="计算过去N周期的价格净变动绝对值与ATR的比值，衡量趋势强度相对于噪音的大小。比值高表示趋势强劲（因子+1），比值低表示震荡或unsure状态（因子-1）。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 14
        close = data['close']
        high = data['high']
        low = data['low']
        atr = (high - low).rolling(n).mean()
        net_move = (close - close.shift(n)).abs()
        ratio = net_move / (atr + 1e-10)
        factor = (ratio - 1).clip(-1, 1)  # 映射到[-1,1]
        return factor
