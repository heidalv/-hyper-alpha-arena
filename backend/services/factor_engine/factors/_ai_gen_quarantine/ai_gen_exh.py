"""AI因子: 趋势衰竭 | 置信:70% | 当RSI进入超买/超卖区且价格远离均线，但RSI斜率开始反转时，预示趋势衰竭。多头超时亏损常发生在超买衰竭阶段，空头超时亏损在超卖衰竭阶段。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendExhaustion(BaseFactor):
    """当RSI进入超买/超卖区且价格远离均线，但RSI斜率开始反转时，预示趋势衰竭。多头超时亏损常发生在超买衰竭阶段，空头超时亏损在超卖衰竭阶段。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_exh",
            name="Trend Exhaustion",
            display_name="趋势衰竭",
            description="当RSI进入超买/超卖区且价格远离均线，但RSI斜率开始反转时，预示趋势衰竭。多头超时亏损常发生在超买衰竭阶段，空头超时亏损在超卖衰竭阶段。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        rsi_period = 14
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        sma = close.rolling(50).mean()
        price_dist = (close - sma) / sma.replace(0, np.nan)
        rsi_slope = rsi.diff(3)
        exhaustion = np.where(rsi > 70, -1, np.where(rsi < 30, 1, 0)).astype(float)
        exhaustion = np.where((rsi > 70) & (rsi_slope < 0) & (price_dist > 0.02), -1, exhaustion)
        exhaustion = np.where((rsi < 30) & (rsi_slope > 0) & (price_dist < -0.02), 1, exhaustion)
        result = pd.Series(exhaustion, index=data.index).fillna(0).clip(-1, 1)
        return result
