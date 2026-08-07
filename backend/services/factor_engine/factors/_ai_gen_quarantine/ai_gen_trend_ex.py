"""AI因子: 趋势衰竭指标 | 置信:60% | 价格偏离移动平均的程度经ATR标准化，结合RSI超买超卖水平，捕捉趋势过度延伸后的反转风险，对应max_hold_timeout亏损模式中趋势停滞。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendExhaustion(BaseFactor):
    """价格偏离移动平均的程度经ATR标准化，结合RSI超买超卖水平，捕捉趋势过度延伸后的反转风险，对应max_hold_timeout亏损模式中趋势停滞。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_ex",
            name="Trend Exhaustion",
            display_name="趋势衰竭指标",
            description="价格偏离移动平均的程度经ATR标准化，结合RSI超买超卖水平，捕捉趋势过度延伸后的反转风险，对应max_hold_timeout亏损模式中趋势停滞。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        atr_period = 14
        ma_period = 20
        rsi_period = 14
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(atr_period).mean()
        ma = close.rolling(ma_period).mean()
        deviation = (close - ma) / atr.replace(0, np.nan)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_norm = (rsi - 50) / 50
        raw = 0.5 * deviation / 3 + 0.5 * rsi_norm
        result = raw.clip(-1, 1)
        return result
