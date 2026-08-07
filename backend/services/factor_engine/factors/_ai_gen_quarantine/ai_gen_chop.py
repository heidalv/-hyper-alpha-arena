"""AI因子: 方向性震荡指数 | 置信:70% | 基于Choppiness Index衡量市场是震荡还是趋势，并结合价格与均线位置赋予方向。值接近0表示震荡市（regime unknown，容易timeout亏损），接近+1为强势上涨趋势，接近-1为强势下跌趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ChoppinessIndexWithDirection(BaseFactor):
    """基于Choppiness Index衡量市场是震荡还是趋势，并结合价格与均线位置赋予方向。值接近0表示震荡市（regime unknown，容易timeout亏损），接近+1为强势上涨趋势，接近-1为强势下跌趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_chop",
            name="Choppiness Index with Direction",
            display_name="方向性震荡指数",
            description="基于Choppiness Index衡量市场是震荡还是趋势，并结合价格与均线位置赋予方向。值接近0表示震荡市（regime unknown，容易timeout亏损），接近+1为强势上涨趋势，接近-1为强势下跌趋势。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        period = 14
        # 真实波幅ATR
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        # 期间最高最低范围
        highest = high.rolling(window=period).max()
        lowest = low.rolling(window=period).min()
        # Choppiness Index 原始值 0-100
        sum_atr = atr * period
        range_hl = highest - lowest
        log10 = np.log10
        chop = 100 * log10(sum_atr / range_hl.replace(0, np.nan)) / log10(period)
        chop = chop.fillna(50).clip(0, 100)
        # 趋势强度 0~1，震荡时接近0，趋势时接近1
        trend_strength = 1 - (chop / 100)
        # 方向：价格在SMA之上的程度，映射到[-1,1]
        sma = close.rolling(window=period).mean()
        direction = (close - sma) / (sma.replace(0, np.nan) + 1e-9)
        direction = direction.apply(lambda x: 2 / (1 + np.exp(-x)) - 1)  # 用tanh类似效果
        # 合成因子：趋势强度 * 方向
        result = trend_strength * direction
        result = result.clip(-1, 1)
        return result
