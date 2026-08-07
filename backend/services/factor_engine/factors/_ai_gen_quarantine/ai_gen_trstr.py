"""AI因子: 趋势强度 | 置信:70% | 利用ADX衡量趋势强度，无趋势(ADX<20)返回负值以提示避开震荡市，强趋势(ADX>40)返回正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrength(BaseFactor):
    """利用ADX衡量趋势强度，无趋势(ADX<20)返回负值以提示避开震荡市，强趋势(ADX>40)返回正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trstr",
            name="Trend Strength",
            display_name="趋势强度",
            description="利用ADX衡量趋势强度，无趋势(ADX<20)返回负值以提示避开震荡市，强趋势(ADX>40)返回正值。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.DataFrame({'h-l': high - low, 'h-pc': (high - close.shift(1)).abs(), 'l-pc': (low - close.shift(1)).abs()}).max(axis=1)
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr = pd.Series(tr, index=data.index).ewm(alpha=1/14, min_periods=14).mean()
        plus_di = 100 * pd.Series(plus_dm, index=data.index).ewm(alpha=1/14, min_periods=14).mean() / atr
        minus_di = 100 * pd.Series(minus_dm, index=data.index).ewm(alpha=1/14, min_periods=14).mean() / atr
        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)).fillna(0)
        adx = dx.ewm(alpha=1/14, min_periods=14).mean()
        result = ((adx - 20) / 20).clip(-1, 1)
        return result.rename('ai_gen_trstr')
