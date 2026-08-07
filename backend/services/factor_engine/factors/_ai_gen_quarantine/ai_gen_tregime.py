"""AI因子: 趋势强度因子 | 置信:70% | 基于DMI计算趋势强度，当ADX低于阈值时认为处于无趋势震荡区（regime=unknown），因子值负向映射，趋势越弱越接近-1，趋势越强越接近+1"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Regime_Strength(BaseFactor):
    """基于DMI计算趋势强度，当ADX低于阈值时认为处于无趋势震荡区（regime=unknown），因子值负向映射，趋势越弱越接近-1，趋势越强越接近+1"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tregime",
            name="Trend_Regime_Strength",
            display_name="趋势强度因子",
            description="基于DMI计算趋势强度，当ADX低于阈值时认为处于无趋势震荡区（regime=unknown），因子值负向映射，趋势越弱越接近-1，趋势越强越接近+1",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high, low, close = data['high'], data['low'], data['close']
        period = 14
        # 计算 +DI 和 -DI
        up_move = high.diff()
        down_move = low.diff()
        up_move[up_move < 0] = 0
        down_move[down_move > 0] = 0
        down_move = down_move.abs()
        # 计算 true range
        tr = np.maximum(high - low, np.abs(high - close.shift()), np.abs(low - close.shift()))
        atr = tr.rolling(period).mean()
        # 平滑 up/down
        up_smooth = up_move.rolling(period).mean()
        down_smooth = down_move.rolling(period).mean()
        plus_di = 100 * (up_smooth / atr)
        minus_di = 100 * (down_smooth / atr)
        # 计算 DX 和 ADX
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(period).mean()
        # 归一化到[-1,1]，阈值25作为中性
        result = (adx - 25) / 25
        result = np.clip(result, -1, 1)
        return result
