"""AI因子: 波动率扩张因子 | 置信:60% | 短期波动率相对长期波动率的扩张程度，反映市场波动突增。实盘亏损中频繁出现止损和利润回吐，往往伴随波动率跳升，该因子可提前预警此类风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityExpansion(BaseFactor):
    """短期波动率相对长期波动率的扩张程度，反映市场波动突增。实盘亏损中频繁出现止损和利润回吐，往往伴随波动率跳升，该因子可提前预警此类风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volregime",
            name="Volatility Expansion",
            display_name="波动率扩张因子",
            description="短期波动率相对长期波动率的扩张程度，反映市场波动突增。实盘亏损中频繁出现止损和利润回吐，往往伴随波动率跳升，该因子可提前预警此类风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        returns = close.pct_change()
        short_vol = returns.rolling(5).std()
        long_vol = returns.rolling(20).std().replace(0, np.nan)
        ratio = short_vol / long_vol - 1
        result = pd.Series(np.tanh(ratio * 5), index=data.index).fillna(0)
        return result
