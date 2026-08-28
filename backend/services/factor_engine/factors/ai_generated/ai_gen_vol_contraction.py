"""AI因子: 波动收缩陷阱因子 | 置信:50% | 针对sl和profit_drawdown_full亏损模式，捕捉波动率急剧收缩后的假突破陷阱。当短期波动率远低于长期波动率且价格突破近期高点时，因子值降低，提示止损风险增大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityContractionTrap(BaseFactor):
    """针对sl和profit_drawdown_full亏损模式，捕捉波动率急剧收缩后的假突破陷阱。当短期波动率远低于长期波动率且价格突破近期高点时，因子值降低，提示止损风险增大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_contraction",
            name="Volatility Contraction Trap",
            display_name="波动收缩陷阱因子",
            description="针对sl和profit_drawdown_full亏损模式，捕捉波动率急剧收缩后的假突破陷阱。当短期波动率远低于长期波动率且价格突破近期高点时，因子值降低，提示止损风险增大。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_vol = data['close'].pct_change().rolling(5).std()
        long_vol = data['close'].pct_change().rolling(50).std()
        vol_ratio = short_vol / (long_vol + 1e-9)
        high_break = data['close'] / data['high'].rolling(20).max()
        result = (vol_ratio - 1) * (high_break - 1).clip(-1, 0) * 2
        return result.clip(-1, 1)
