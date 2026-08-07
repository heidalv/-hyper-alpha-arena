"""AI因子: 量价背离 | 置信:60% | 价格创近期新高/新低但成交量萎缩，揭示趋势动能减弱，往往导致持仓超时亏损。值域[-1,1]反映空头/多头量价背离。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeDryUp(BaseFactor):
    """价格创近期新高/新低但成交量萎缩，揭示趋势动能减弱，往往导致持仓超时亏损。值域[-1,1]反映空头/多头量价背离。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_dry",
            name="Volume Dry-Up",
            display_name="量价背离",
            description="价格创近期新高/新低但成交量萎缩，揭示趋势动能减弱，往往导致持仓超时亏损。值域[-1,1]反映空头/多头量价背离。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        window = 20
        highest = close.rolling(window).max()
        lowest = close.rolling(window).min()
        vol_ma = volume.rolling(window).mean()
        vol_ratio = volume / vol_ma.replace(0, np.nan)
        new_high = (close == highest) & (vol_ratio < 0.8)
        new_low = (close == lowest) & (vol_ratio < 0.8)
        signal = np.where(new_high, -1, np.where(new_low, 1, 0))
        result = pd.Series(signal, index=data.index).rolling(3).mean().clip(-1, 1)
        return result
