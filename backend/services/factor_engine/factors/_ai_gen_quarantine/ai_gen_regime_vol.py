"""AI因子: 波动率扩张反转因子 | 置信:60% | 衡量短期波动率（5日）与长期波动率（20日）之比，结合收盘价在近期价格区间中的位置（percent rank）。当波动率比值高且价格处于极端分位数（>90%或<10%）时，预示市场可能进入反转阶段。输出综合信号的归一化值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeVolatilityExpansionReversal(BaseFactor):
    """衡量短期波动率（5日）与长期波动率（20日）之比，结合收盘价在近期价格区间中的位置（percent rank）。当波动率比值高且价格处于极端分位数（>90%或<10%）时，预示市场可能进入反转阶段。输出综合信号的归一化值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_vol",
            name="Regime Volatility Expansion Reversal",
            display_name="波动率扩张反转因子",
            description="衡量短期波动率（5日）与长期波动率（20日）之比，结合收盘价在近期价格区间中的位置（percent rank）。当波动率比值高且价格处于极端分位数（>90%或<10%）时，预示市场可能进入反转阶段。输出综合信号的归一化值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 波动率：用ATR度量
        tr = pd.concat([data['high'] - data['low'],
                        abs(data['high'] - data['close'].shift()),
                        abs(data['low'] - data['close'].shift())], axis=1).max(axis=1)
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        ratio = atr_short / atr_long
        # 价格百分位（20日区间）
        rolling_low = data['close'].rolling(20).min()
        rolling_high = data['close'].rolling(20).max()
        pct_rank = (data['close'] - rolling_low) / (rolling_high - rolling_low + 1e-10)
        # 极端位置信号: 接近1或0时反转信号
        extreme = 0.5 - abs(pct_rank - 0.5)  # 越极端值越大
        extreme = extreme * 2  # 放大到[0,1]
        # 组合: 高波动扩张+极端位置
        signal = ratio * extreme
        # 中心化并缩放
        result = (signal - signal.mean()) / (signal.std() + 1e-10)
        result = result.clip(-3, 3) / 3
        return pd.Series(result, index=data.index).fillna(0)
