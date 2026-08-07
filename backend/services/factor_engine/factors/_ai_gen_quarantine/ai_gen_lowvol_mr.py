"""AI因子: 低波均值回归 | 置信:60% | 当市场波动率压缩至极低水平（regime unknown）时，持仓过久易导致hold_timeout亏损，此时价格往往呈现均值回归特征。结合RSI超买超卖发出反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LowVolatilityMeanReversion(BaseFactor):
    """当市场波动率压缩至极低水平（regime unknown）时，持仓过久易导致hold_timeout亏损，此时价格往往呈现均值回归特征。结合RSI超买超卖发出反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lowvol_mr",
            name="Low Volatility Mean Reversion",
            display_name="低波均值回归",
            description="当市场波动率压缩至极低水平（regime unknown）时，持仓过久易导致hold_timeout亏损，此时价格往往呈现均值回归特征。结合RSI超买超卖发出反转信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        high = data['high']
        low = data['low']
        # 波动率压缩：布林带宽度处于历史低位
        bb_period = 20
        bb_std = 2
        ma = close.rolling(bb_period).mean()
        std = close.rolling(bb_period).std()
        bb_width = (ma + bb_std * std - (ma - bb_std * std)) / ma
        bb_width_low = bb_width.rolling(100).rank(pct=True) < 0.2  # 20%分位
        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = -delta.clip(upper=0).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        overbought = rsi > 70
        oversold = rsi < 30
        signal = pd.Series(0.0, index=data.index)
        signal[bb_width_low & overbought] = -1.0
        signal[bb_width_low & oversold] = 1.0
        # 强度由RSI极端程度决定
        rsi_strength = (rsi - 50).abs() / 20  # 偏离50越多越强
        rsi_strength = rsi_strength.clip(0, 1)
        result = signal * rsi_strength
        return result
