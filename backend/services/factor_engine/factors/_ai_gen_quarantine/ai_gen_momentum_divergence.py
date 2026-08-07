"""AI因子: 量价动量背离 | 置信:60% | 比较短期和长期价格动量，结合成交量放大/萎缩，捕捉动量衰竭。短期动量弱于长期且量缩时偏空，避免追多超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumDivergenceWithVolume(BaseFactor):
    """比较短期和长期价格动量，结合成交量放大/萎缩，捕捉动量衰竭。短期动量弱于长期且量缩时偏空，避免追多超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_divergence",
            name="Momentum Divergence with Volume",
            display_name="量价动量背离",
            description="比较短期和长期价格动量，结合成交量放大/萎缩，捕捉动量衰竭。短期动量弱于长期且量缩时偏空，避免追多超时亏损。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close'].values
        volume = data['volume'].values
        short_period = 5
        long_period = 20
        mom_short = pd.Series(close).pct_change(short_period).values
        mom_long = pd.Series(close).pct_change(long_period).values
        mom_diff = mom_short - mom_long
        vol_mean_short = pd.Series(volume).rolling(short_period).mean().values
        vol_mean_long = pd.Series(volume).rolling(long_period).mean().values
        vol_ratio = np.divide(vol_mean_short, vol_mean_long, where=vol_mean_long>0, out=np.ones_like(vol_mean_short))
        vol_ratio = np.clip(vol_ratio, 0.5, 2.0)
        raw = mom_diff * (vol_ratio - 1.0)
        raw = np.nan_to_num(raw, nan=0.0)
        denom = np.std(raw[-long_period*2:]) if np.std(raw[-long_period*2:]) != 0 else 1e-6
        result = raw / denom
        result = np.tanh(result)
        return pd.Series(result, index=data.index).fillna(0)
