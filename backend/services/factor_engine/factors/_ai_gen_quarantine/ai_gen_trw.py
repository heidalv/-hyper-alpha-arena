"""AI因子: 趋势弱势因子 | 置信:70% | 基于ADX指标衡量趋势强度，当市场处于无趋势或弱趋势时，做多容易出现亏损。计算14周期ADX并归一化到[-1,1]，低ADX对应负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Weakness(BaseFactor):
    """基于ADX指标衡量趋势强度，当市场处于无趋势或弱趋势时，做多容易出现亏损。计算14周期ADX并归一化到[-1,1]，低ADX对应负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trw",
            name="Trend_Weakness",
            display_name="趋势弱势因子",
            description="基于ADX指标衡量趋势强度，当市场处于无趋势或弱趋势时，做多容易出现亏损。计算14周期ADX并归一化到[-1,1]，低ADX对应负值。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        up = high - high.shift(1)
        down = low.shift(1) - low
        plus_dm = np.where((up > down) & (up > 0), up, 0)
        minus_dm = np.where((down > up) & (down > 0), down, 0)
        plus_di = 100 * pd.Series(plus_dm).rolling(14).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(14).mean() / atr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean()
        # 将ADX从0-100映射到1到-1，阈值25以下为弱势
        result = 1 - 2 * (adx / 100)
        return result.clip(-1, 1)
