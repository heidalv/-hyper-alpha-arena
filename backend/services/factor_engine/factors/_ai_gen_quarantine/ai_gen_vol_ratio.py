"""AI因子: 波动率扩张比率 | 置信:60% | 短期ATR(10)与长期ATR(30)的比值减1，经tanh映射到[-1,1]。正值表示波动率扩张，趋势可能加速，反向策略易亏损；负值表示波动率收缩，震荡市中反向策略可能盈利。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityExpansionRatio(BaseFactor):
    """短期ATR(10)与长期ATR(30)的比值减1，经tanh映射到[-1,1]。正值表示波动率扩张，趋势可能加速，反向策略易亏损；负值表示波动率收缩，震荡市中反向策略可能盈利。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_ratio",
            name="Volatility Expansion Ratio",
            display_name="波动率扩张比率",
            description="短期ATR(10)与长期ATR(30)的比值减1，经tanh映射到[-1,1]。正值表示波动率扩张，趋势可能加速，反向策略易亏损；负值表示波动率收缩，震荡市中反向策略可能盈利。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        def atr(period):
            tr = pd.concat([high - low,
                            (high - close.shift()).abs(),
                            (low - close.shift()).abs()], axis=1).max(axis=1)
            return tr.rolling(period).mean()
        atr10 = atr(10)
        atr30 = atr(30)
        ratio = atr10 / atr30 - 1.0
        ratio = ratio.replace([np.inf, -np.inf], 0).fillna(0)
        result = np.tanh(ratio * 5)  # 放大系数5
        return result
