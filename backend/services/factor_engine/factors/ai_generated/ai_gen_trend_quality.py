"""AI因子: 趋势质量因子 | 置信:60% | 基于ADX与方向指标，衡量当前趋势的明确程度与方向。趋势越强因子越接近+1（上升）或-1（下降），震荡市接近0。震荡市可能造成反复止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendQuality(BaseFactor):
    """基于ADX与方向指标，衡量当前趋势的明确程度与方向。趋势越强因子越接近+1（上升）或-1（下降），震荡市接近0。震荡市可能造成反复止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_quality",
            name="trend_quality",
            display_name="趋势质量因子",
            description="基于ADX与方向指标，衡量当前趋势的明确程度与方向。趋势越强因子越接近+1（上升）或-1（下降），震荡市接近0。震荡市可能造成反复止损。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        high, low, close = df['high'], df['low'], df['close']
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr = pd.concat([(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(14).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(14).mean() / atr)
        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di)) * 100
        adx = dx.rolling(14).mean()
        direction = np.sign(plus_di - minus_di).fillna(0)
        factor = (adx / 100) * direction
        return factor.fillna(0)
