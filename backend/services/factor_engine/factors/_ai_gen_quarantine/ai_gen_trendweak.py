"""AI因子: 趋势弱度因子 | 置信:70% | 基于ADX指标，当ADX低于20时表示市场无明确趋势，做多风险较高。因子值负向表示趋势弱，不适合做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendWeaknessIndicator(BaseFactor):
    """基于ADX指标，当ADX低于20时表示市场无明确趋势，做多风险较高。因子值负向表示趋势弱，不适合做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendweak",
            name="Trend Weakness Indicator",
            display_name="趋势弱度因子",
            description="基于ADX指标，当ADX低于20时表示市场无明确趋势，做多风险较高。因子值负向表示趋势弱，不适合做多。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算TR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 计算+DM和-DM
        up_move = high - high.shift()
        down_move = low.shift() - low
        pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        pos_dm_series = pd.Series(pos_dm, index=high.index).rolling(14).mean()
        neg_dm_series = pd.Series(neg_dm, index=high.index).rolling(14).mean()
        # 计算DI+
        di_plus = 100 * pos_dm_series / atr
        di_minus = 100 * neg_dm_series / atr
        dx = 100 * ((di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan))
        adx = dx.rolling(14).mean()
        # 归一化到[-1,1]：ADX<20时负，>20时正，截断     factor = (20 - adx) / 20.0
        factor = factor.clip(-1, 1)
        # 前14天无值填充0     factor = factor.fillna(0)
        return factor
