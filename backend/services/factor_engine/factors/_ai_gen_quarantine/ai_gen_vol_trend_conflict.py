"""AI因子: 波动趋势冲突因子 | 置信:65% | 当市场波动率增大但趋势强度弱时，价格易出现反复震荡，导致止损触发。该因子通过ATR与ADX的比值衡量冲突程度，比值越高越可能处于无序状态，输出负值提示风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityTrendConflictIndicator(BaseFactor):
    """当市场波动率增大但趋势强度弱时，价格易出现反复震荡，导致止损触发。该因子通过ATR与ADX的比值衡量冲突程度，比值越高越可能处于无序状态，输出负值提示风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_trend_conflict",
            name="Volatility-Trend Conflict Indicator",
            display_name="波动趋势冲突因子",
            description="当市场波动率增大但趋势强度弱时，价格易出现反复震荡，导致止损触发。该因子通过ATR与ADX的比值衡量冲突程度，比值越高越可能处于无序状态，输出负值提示风险。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high, low, close = data['high'], data['low'], data['close']
        df = pd.DataFrame({'high': high, 'low': low, 'close': close})
        # ATR (14)
        tr = np.maximum(high - low, np.abs(high - close.shift()), np.abs(low - close.shift()))
        atr = tr.rolling(14).mean()
        # ADX (14)
        up = high.diff()
        down = -low.diff()
        dm_plus = np.where((up > down) & (up > 0), up, 0)
        dm_minus = np.where((down > up) & (down > 0), down, 0)
        tr_s = tr.rolling(14).sum()
        di_plus = 100 * pd.Series(dm_plus).rolling(14).sum() / tr_s
        di_minus = 100 * pd.Series(dm_minus).rolling(14).sum() / tr_s
        dx = 100 * np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10)
        adx = dx.rolling(14).mean()
        # ratio
        ratio = atr / (adx + 1e-10)
        # normalize to [-1,1] using z-score cap at 3
        z = (ratio - ratio.mean()) / ratio.std()
        result = np.clip(z / 3, -1, 1)
        return pd.Series(result, index=data.index).fillna(0)
