"""AI因子: 波动量能背离因子 | 置信:60% | 检测价格波动率（ATR）与成交量的背离现象。当波动率扩大但成交量不增甚至萎缩时，常为假突破信号，容易导致止损亏损，因子为负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Volume_Divergence(BaseFactor):
    """检测价格波动率（ATR）与成交量的背离现象。当波动率扩大但成交量不增甚至萎缩时，常为假突破信号，容易导致止损亏损，因子为负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vvd",
            name="Volatility-Volume Divergence",
            display_name="波动量能背离因子",
            description="检测价格波动率（ATR）与成交量的背离现象。当波动率扩大但成交量不增甚至萎缩时，常为假突破信号，容易导致止损亏损，因子为负值。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR (14周期)
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift(1))
        low_close = np.abs(data['low'] - data['close'].shift(1))
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 计算成交量相对变化
        vol_change = data['volume'].pct_change(5)  # 5期变化
        # ATR变化率
        atr_change = atr.pct_change(5)
        # 背离：ATR上升但成交量下降 => 负值
        raw = atr_change - vol_change
        # 标准化并压缩
        raw = raw.replace([np.inf, -np.inf], 0).fillna(0)
        result = np.tanh(raw * 5)  # 缩放
        return result
