"""AI因子: 低动量陷阱因子 | 置信:55% | 短期动量（5日收益率）为负且成交量较20日均量显著放大时，表明抛压沉重，做多容易在反弹中亏损。该因子利用量价背离识别弱势市场环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Low_Momentum_Trap(BaseFactor):
    """短期动量（5日收益率）为负且成交量较20日均量显著放大时，表明抛压沉重，做多容易在反弹中亏损。该因子利用量价背离识别弱势市场环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lowmom",
            name="Low Momentum Trap",
            display_name="低动量陷阱因子",
            description="短期动量（5日收益率）为负且成交量较20日均量显著放大时，表明抛压沉重，做多容易在反弹中亏损。该因子利用量价背离识别弱势市场环境。",
            category="behavioral",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        ret5 = close.pct_change(5)
        vol_ma20 = volume.rolling(20, min_periods=20).mean()
        vol_ratio = volume / vol_ma20
        # 负收益且放量时，因子为负；放量程度越高越负
        factor = -np.clip(-ret5, 0, 0.05) / 0.05 * np.clip(vol_ratio - 1, 0, 2) / 2
        return pd.Series(np.clip(factor, -1, 0), index=data.index)
