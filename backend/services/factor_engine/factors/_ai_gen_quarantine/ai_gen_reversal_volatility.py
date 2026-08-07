"""AI因子: 反转波动状态 | 置信:60% | 利用波动率结构突变和价格极端值识别反转倾向。计算ATR（平均真实范围）的短期和长期比率，结合当前价格在布林带中的位置。当波动率激增且价格触及极端带时，预示反转。正值表示看多反转，负值看空反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalVolatilityRegime(BaseFactor):
    """利用波动率结构突变和价格极端值识别反转倾向。计算ATR（平均真实范围）的短期和长期比率，结合当前价格在布林带中的位置。当波动率激增且价格触及极端带时，预示反转。正值表示看多反转，负值看空反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_volatility",
            name="Reversal Volatility Regime",
            display_name="反转波动状态",
            description="利用波动率结构突变和价格极端值识别反转倾向。计算ATR（平均真实范围）的短期和长期比率，结合当前价格在布林带中的位置。当波动率激增且价格触及极端带时，预示反转。正值表示看多反转，负值看空反转。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        high = data['high']
        low = data['low']
        close = data['close']
    
        # 真实波幅TR
        prev_close = close.shift(1)
        tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    
        # 短期ATR(5)与长期ATR(20)的比率
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        atr_ratio = atr_short / atr_long
    
        # 布林带：20日均线±2倍标准差
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
    
        # 价格触及上轨和下轨
        touch_upper = (close >= upper * 0.995).astype(float)
        touch_lower = (close <= lower * 1.005).astype(float)
    
        # 波动率激增（比长期均值高50%）
        vol_spike = (atr_ratio > 1.5).astype(float)
    
        # 做空反转：上轨+波动率激增
        short_signal = touch_upper * vol_spike
        # 做多反转：下轨+波动率激增
        long_signal = touch_lower * vol_spike
    
        result = long_signal - short_signal
        result = result.rolling(3).mean()
        result = result.clip(-1, 1)
        return result
