"""AI因子: 波动率状态因子 | 置信:60% | 基于ATR与历史平均ATR的比值，判断当前市场波动率相对于近期水平的高低。高波动率环境有利于趋势跟踪，低波动率易导致假突破和止损亏损。因子值在-1到1之间，正值表示高波动（趋势友好），负值表示低波动（震荡风险）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeIndicator(BaseFactor):
    """基于ATR与历史平均ATR的比值，判断当前市场波动率相对于近期水平的高低。高波动率环境有利于趋势跟踪，低波动率易导致假突破和止损亏损。因子值在-1到1之间，正值表示高波动（趋势友好），负值表示低波动（震荡风险）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volty_regime",
            name="Volatility Regime Indicator",
            display_name="波动率状态因子",
            description="基于ATR与历史平均ATR的比值，判断当前市场波动率相对于近期水平的高低。高波动率环境有利于趋势跟踪，低波动率易导致假突破和止损亏损。因子值在-1到1之间，正值表示高波动（趋势友好），负值表示低波动（震荡风险）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(window=14, min_periods=1).mean()
        # 计算ATR的相对变化 (当前ATR / 过去20期平均ATR - 1) 并压缩到[-1,1]
        avg_atr = atr.rolling(window=20, min_periods=1).mean()
        ratio = atr / avg_atr
        # 用tanh压缩到[-1,1]，中心点为1
        result = np.tanh((ratio - 1) * 3)
        # 处理前14个NaN用第一个有效值填充
        result = result.fillna(method='bfill')
        return pd.Series(result, index=data.index)
