"""AI因子: 均值回复极端因子 | 置信:70% | 结合乖离率和RSI极端值，识别价格过度延伸后的反转机会。计算收盘价相对于20日均线的百分比偏离，以及14日RSI。当偏离>5%且RSI>70（超买）或偏离<-5%且RSI<30（超卖）时，给出反转信号。超买时卖出（负值），超卖时买入（正值）。输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Extreme(BaseFactor):
    """结合乖离率和RSI极端值，识别价格过度延伸后的反转机会。计算收盘价相对于20日均线的百分比偏离，以及14日RSI。当偏离>5%且RSI>70（超买）或偏离<-5%且RSI<30（超卖）时，给出反转信号。超买时卖出（负值），超卖时买入（正值）。输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_meank",
            name="Mean Reversion Extreme",
            display_name="均值回复极端因子",
            description="结合乖离率和RSI极端值，识别价格过度延伸后的反转机会。计算收盘价相对于20日均线的百分比偏离，以及14日RSI。当偏离>5%且RSI>70（超买）或偏离<-5%且RSI<30（超卖）时，给出反转信号。超买时卖出（负值），超卖时买入（正值）。输出[-1,1]。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 20-day SMA
        sma = close.rolling(20).mean()
        # Percentage deviation
        dev = (close - sma) / sma
        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # Conditions
        overbought = (rsi > 70) & (dev > 0.05)
        oversold = (rsi < 30) & (dev < -0.05)
        # Signal: +1 for oversold (buy), -1 for overbought (sell), 0 otherwise
        factor = pd.Series(0, index=close.index)
        factor[oversold] = 1.0
        factor[overbought] = -1.0
        # Smooth slightly? No, keep binary for clarity
        return factor
