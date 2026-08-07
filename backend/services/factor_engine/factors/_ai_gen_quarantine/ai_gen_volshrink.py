"""AI因子: 成交量萎缩预警因子 | 置信:60% | 当成交量相对过去20日均值显著萎缩（低于0.6倍）且价格窄幅震荡（ATR下降），市场流动性不足，容易导致滑点或无法及时平仓（对应master_running_close_tiny亏损模式）。因子在成交量萎缩时给出负值，正常时接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Shrinkage_Warning(BaseFactor):
    """当成交量相对过去20日均值显著萎缩（低于0.6倍）且价格窄幅震荡（ATR下降），市场流动性不足，容易导致滑点或无法及时平仓（对应master_running_close_tiny亏损模式）。因子在成交量萎缩时给出负值，正常时接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volshrink",
            name="Volume Shrinkage Warning",
            display_name="成交量萎缩预警因子",
            description="当成交量相对过去20日均值显著萎缩（低于0.6倍）且价格窄幅震荡（ATR下降），市场流动性不足，容易导致滑点或无法及时平仓（对应master_running_close_tiny亏损模式）。因子在成交量萎缩时给出负值，正常时接近0。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        volume = data['volume'].values
        close = data['close'].values
        high = data['high'].values
        low = data['low'].values
        # 成交量相对20日均值
        vol_ma = pd.Series(volume).rolling(20).mean().values
        vol_ratio = volume / (vol_ma + 1e-10)
        # 价格窄幅：ATR下降（5日ATR < 20日ATR的70%）
        tr = np.maximum(high - low, np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1)))
        tr[0] = high[0] - low[0]
        atr5 = pd.Series(tr).rolling(5).mean().values
        atr20 = pd.Series(tr).rolling(20).mean().values
        atr_shrink = atr5 / (atr20 + 1e-10) < 0.7
        # 成交量萎缩且ATR降低
        condition = (vol_ratio < 0.6) & atr_shrink
        result = np.where(condition, -1.0, 0.0)
        result = pd.Series(result).rolling(3).mean().fillna(0).clip(-1, 1).values
        return pd.Series(result, index=data.index)
