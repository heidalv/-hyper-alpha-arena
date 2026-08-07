"""AI因子: 波动率异常因子 | 置信:55% | 计算近期波动率相对长期波动率的变化率。当变化率绝对值过大时，市场可能进入不明确状态。通过ATR比率，映射到[-1,1]，负值表示异常波动。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatilityregimefactor(BaseFactor):
    """计算近期波动率相对长期波动率的变化率。当变化率绝对值过大时，市场可能进入不明确状态。通过ATR比率，映射到[-1,1]，负值表示异常波动。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vrg",
            name="VolatilityRegimeFactor",
            display_name="波动率异常因子",
            description="计算近期波动率相对长期波动率的变化率。当变化率绝对值过大时，市场可能进入不明确状态。通过ATR比率，映射到[-1,1]，负值表示异常波动。",
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
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr_short = tr.rolling(7).mean()
        atr_long = tr.rolling(30).mean()
        ratio = atr_short / atr_long - 1  # 变化率
        # 映射：当比率在-0.2~0.2之间视为正常趋势，接近0；超出-0.5或0.5视为异常，因子接近-1
        factor = np.where(ratio < -0.5, -1, np.where(ratio > 0.5, -1, np.where(ratio < -0.2, (ratio + 0.5) / 0.3 * 2 - 1, np.where(ratio > 0.2, (0.5 - ratio) / 0.3 * 2 - 1, 0))))
        factor = np.clip(factor, -1, 1)
        return pd.Series(factor, index=data.index)
