"""AI因子: ADX趋势强度 | 置信:60% | 基于平均趋向指数(ADX)衡量趋势强度，ADX值低表示盘整市场，此时趋势跟踪策略容易亏损。因子值正向化：ADX高于50为正，低于50为负，映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ADXTrendStrength(BaseFactor):
    """基于平均趋向指数(ADX)衡量趋势强度，ADX值低表示盘整市场，此时趋势跟踪策略容易亏损。因子值正向化：ADX高于50为正，低于50为负，映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adx",
            name="ADX Trend Strength",
            display_name="ADX趋势强度",
            description="基于平均趋向指数(ADX)衡量趋势强度，ADX值低表示盘整市场，此时趋势跟踪策略容易亏损。因子值正向化：ADX高于50为正，低于50为负，映射到[-1,1]。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        period = 14
        # 计算典型价格
        high = df['high']
        low = df['low']
        close = df['close']
        # 计算 +DM, -DM
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=df.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=df.index)
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        # 平滑
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
        adx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di)).rolling(window=period).mean()
        # 映射到[-1,1]: 以50为中心
        factor = (adx - 50) / 50
        factor = factor.clip(-1, 1)
        return factor
