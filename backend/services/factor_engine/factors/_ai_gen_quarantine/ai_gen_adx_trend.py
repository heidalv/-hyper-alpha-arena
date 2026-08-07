"""AI因子: ADX趋势强度 | 置信:60% | 当ADX低于20时市场趋势不明朗（regime unknown），多头易亏损；ADX高于25时趋势明确。因子值正表示强趋势，负表示弱趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ADX_Trend_Strength(BaseFactor):
    """当ADX低于20时市场趋势不明朗（regime unknown），多头易亏损；ADX高于25时趋势明确。因子值正表示强趋势，负表示弱趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adx_trend",
            name="ADX Trend Strength",
            display_name="ADX趋势强度",
            description="当ADX低于20时市场趋势不明朗（regime unknown），多头易亏损；ADX高于25时趋势明确。因子值正表示强趋势，负表示弱趋势。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算+DI和-DI
        up = high - high.shift(1)
        down = low.shift(1) - low
        plus_dm = np.where((up > down) & (up > 0), up, 0)
        minus_dm = np.where((down > up) & (down > 0), down, 0)
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        tr14 = tr.rolling(14).sum()
        plus_di = 100 * (pd.Series(plus_dm).rolling(14).sum() / tr14)
        minus_di = 100 * (pd.Series(minus_dm).rolling(14).sum() / tr14)
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(14).mean()
        # 映射：ADX<20 -> -1, ADX>25 -> +1, 中间线性
        result = np.clip((adx - 20) / (25 - 20) * 2 - 1, -1, 1)
        result = pd.Series(result, index=data.index).fillna(0)
        return result
