"""AI因子: 布林带反转概率 | 置信:60% | 基于布林带宽度和RSI极端值，判断均值回归概率。带宽较窄时容易爆发趋势，带宽极宽时容易回归。结合超买超卖，输出反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bandreversalprobability(BaseFactor):
    """基于布林带宽度和RSI极端值，判断均值回归概率。带宽较窄时容易爆发趋势，带宽极宽时容易回归。结合超买超卖，输出反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal",
            name="BandReversalProbability",
            display_name="布林带反转概率",
            description="基于布林带宽度和RSI极端值，判断均值回归概率。带宽较窄时容易爆发趋势，带宽极宽时容易回归。结合超买超卖，输出反转信号。",
            category="technical",
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
    
        # 布林带
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        band_width = (upper - lower) / ma20
    
        # 价格在带内的位置
        position = (close - lower) / (upper - lower + 1e-10)  # 0~1
    
        # RSI 14
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
    
        # 信号：带宽宽 + 超买 -> 空头；带宽宽 + 超卖 -> 多头
        width_high = (band_width > band_width.rolling(50).mean() * 1.5).astype(float)
        overbought = (rsi > 75).astype(float)
        oversold = (rsi < 25).astype(float)
    
        short = width_high * overbought * -1.0
        long = width_high * oversold * 1.0
        result = short + long
        return result
