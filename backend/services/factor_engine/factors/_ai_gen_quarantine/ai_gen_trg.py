"""AI因子: 趋势状态因子 | 置信:60% | 基于ADX指标判断趋势强度。ADX<25视为弱趋势（regime=unknown），因子值接近-1；ADX>40视为强趋势，因子值接近+1。中间线性映射。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendregimefactor(BaseFactor):
    """基于ADX指标判断趋势强度。ADX<25视为弱趋势（regime=unknown），因子值接近-1；ADX>40视为强趋势，因子值接近+1。中间线性映射。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trg",
            name="TrendRegimeFactor",
            display_name="趋势状态因子",
            description="基于ADX指标判断趋势强度。ADX<25视为弱趋势（regime=unknown），因子值接近-1；ADX>40视为强趋势，因子值接近+1。中间线性映射。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算平均真实波幅ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 计算+DM和-DM
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        plus_dm_series = pd.Series(plus_dm, index=data.index).rolling(14).mean()
        minus_dm_series = pd.Series(minus_dm, index=data.index).rolling(14).mean()
        # 计算+DI和-DI
        plus_di = 100 * plus_dm_series / atr
        minus_di = 100 * minus_dm_series / atr
        # 计算DX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean()
        # 映射到[-1,1]
        adx = adx.fillna(25)
        factor = np.where(adx < 25, (adx / 25) * 2 - 1, np.where(adx > 40, 1, (adx - 25) / 15 * 2 - 1))
        return pd.Series(factor, index=data.index)
