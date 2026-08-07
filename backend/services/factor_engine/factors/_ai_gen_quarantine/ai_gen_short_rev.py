"""AI因子: 短期超买反转 | 置信:60% | 价格短期内快速上涨（RSI>70且连续3日上涨），容易引发回调导致止损或止盈亏损。因子负值表示超买区域。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Short_term_Overbought_Reversal(BaseFactor):
    """价格短期内快速上涨（RSI>70且连续3日上涨），容易引发回调导致止损或止盈亏损。因子负值表示超买区域。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_rev",
            name="Short-term Overbought Reversal",
            display_name="短期超买反转",
            description="价格短期内快速上涨（RSI>70且连续3日上涨），容易引发回调导致止损或止盈亏损。因子负值表示超买区域。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算14日RSI
        delta = data['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # 连续3日上涨
        up_days = (data['close'] > data['close'].shift(1)).rolling(3).sum()
        condition = (rsi > 70) & (up_days >= 3)
        result = -1.0 * condition.astype(float) + 0.0
        return result.fillna(0.0)
