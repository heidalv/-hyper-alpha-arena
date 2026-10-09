"""AI因子: RSI极值反转 | 置信:55% | 基于14日RSI的极值反转：RSI高于70视为超买（因子为负，预期回落），低于30视为超卖（因子为正，预期反弹）。用(50-RSI)/50线性映射并截断。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSIExtremeReversal(BaseFactor):
    """基于14日RSI的极值反转：RSI高于70视为超买（因子为负，预期回落），低于30视为超卖（因子为正，预期反弹）。用(50-RSI)/50线性映射并截断。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rsi_extreme_rev",
            name="RSI Extreme Reversal",
            display_name="RSI极值反转",
            description="基于14日RSI的极值反转：RSI高于70视为超买（因子为负，预期回落），低于30视为超卖（因子为正，预期反弹）。用(50-RSI)/50线性映射并截断。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean() + 1e-9
        rs = avg_gain / avg_loss
        rsi = 100 - 100 / (1 + rs)
        result = ((50 - rsi) / 50).clip(-1, 1)
        return result
