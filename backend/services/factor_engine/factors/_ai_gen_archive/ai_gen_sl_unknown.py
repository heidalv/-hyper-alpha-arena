"""AI因子: 未知状态止损波动尖峰 | 置信:50% | 止损亏损多发生在regime=unknown且波动率异常。当短期波动率远超长期且价格处于下跌趋势时，空头容易遭遇反弹止损。该因子在波动率尖峰且价格低于均线时输出负值（看多），规避空头。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class SLUnknownVolatilitySpike(BaseFactor):
    """止损亏损多发生在regime=unknown且波动率异常。当短期波动率远超长期且价格处于下跌趋势时，空头容易遭遇反弹止损。该因子在波动率尖峰且价格低于均线时输出负值（看多），规避空头。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_unknown",
            name="SL_Unknown_Volatility_Spike",
            display_name="未知状态止损波动尖峰",
            description="止损亏损多发生在regime=unknown且波动率异常。当短期波动率远超长期且价格处于下跌趋势时，空头容易遭遇反弹止损。该因子在波动率尖峰且价格低于均线时输出负值（看多），规避空头。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_vol = data['close'].pct_change().rolling(3).std()
        long_vol = data['close'].pct_change().rolling(30).std()
        vol_spike = short_vol / (long_vol + 1e-9)
        ma = data['close'].rolling(10).mean()
        below_ma = (data['close'] < ma).astype(float)
        result = (-1 * vol_spike * below_ma * (1 - data['close'].pct_change(5).abs())).clip(-1, 1)
        return result
