"""AI因子: 未知状态反转因子 | 置信:55% | 针对regime=unknown时出现的亏损，这些交易往往发生在市场状态不明确、趋势快速反转的时期。使用价格偏离长期均线的程度与短期动量方向的反向关系，当价格大幅偏离均线且短期动量转弱时，预示反转风险，因子值降低。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeBreakReversal(BaseFactor):
    """针对regime=unknown时出现的亏损，这些交易往往发生在市场状态不明确、趋势快速反转的时期。使用价格偏离长期均线的程度与短期动量方向的反向关系，当价格大幅偏离均线且短期动量转弱时，预示反转风险，因子值降低。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_break",
            name="Regime_Break_Reversal",
            display_name="未知状态反转因子",
            description="针对regime=unknown时出现的亏损，这些交易往往发生在市场状态不明确、趋势快速反转的时期。使用价格偏离长期均线的程度与短期动量方向的反向关系，当价格大幅偏离均线且短期动量转弱时，预示反转风险，因子值降低。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma_long = data['close'].rolling(50).mean()
        ma_short = data['close'].rolling(10).mean()
        price_dev = (data['close'] - ma_long) / (ma_long + 1e-9)
        momentum = (ma_short - ma_long) / (ma_long + 1e-9)
        result = (momentum - price_dev).clip(-1, 1)
        return result
