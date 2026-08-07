"""AI因子: 波动成交量背离因子 | 置信:60% | 当价格波动率与成交量变化方向相反时，市场流动性不足或分歧加大，易导致趋势不稳定和意外亏损。计算近期价格波动率（ATR/收盘价）的Z-score与成交量变化率的Z-score的差值，并取负值，使上升趋势中量价背离时为负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Volume_Divergence(BaseFactor):
    """当价格波动率与成交量变化方向相反时，市场流动性不足或分歧加大，易导致趋势不稳定和意外亏损。计算近期价格波动率（ATR/收盘价）的Z-score与成交量变化率的Z-score的差值，并取负值，使上升趋势中量价背离时为负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vv",
            name="Volatility_Volume_Divergence",
            display_name="波动成交量背离因子",
            description="当价格波动率与成交量变化方向相反时，市场流动性不足或分歧加大，易导致趋势不稳定和意外亏损。计算近期价格波动率（ATR/收盘价）的Z-score与成交量变化率的Z-score的差值，并取负值，使上升趋势中量价背离时为负值。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # 计算ATR
        tr = pd.concat([data['high'] - data['low'], abs(data['high'] - data['close'].shift(1)), abs(data['low'] - data['close'].shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        atr_norm = atr / data['close']  # 相对波动率
        # 计算成交量变化率
        volume_roc = data['volume'].pct_change(20)
        # Z-score标准化
        atr_z = (atr_norm - atr_norm.rolling(60).mean()) / atr_norm.rolling(60).std()
        vol_z = (volume_roc - volume_roc.rolling(60).mean()) / volume_roc.rolling(60).std()
        # 背离程度：波动率上升而成交量下降 => 负值
        divergence = atr_z - vol_z
        # 归一化到[-1,1]
        result = np.tanh(divergence) * -1  # 取负使背离时接近-1
        return result.fillna(0)
