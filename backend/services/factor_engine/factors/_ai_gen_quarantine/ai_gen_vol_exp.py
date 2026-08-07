"""AI因子: 波动率扩张做空风险 | 置信:60% | 短期波动率与长期波动率之比，反映市场不稳定程度。比值快速上升时，做空易被反向波动止损。因子值越接近+1，表示做空风险越高；越接近-1，表示市场平稳适合做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityExpansionRisk(BaseFactor):
    """短期波动率与长期波动率之比，反映市场不稳定程度。比值快速上升时，做空易被反向波动止损。因子值越接近+1，表示做空风险越高；越接近-1，表示市场平稳适合做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_exp",
            name="Volatility Expansion Risk",
            display_name="波动率扩张做空风险",
            description="短期波动率与长期波动率之比，反映市场不稳定程度。比值快速上升时，做空易被反向波动止损。因子值越接近+1，表示做空风险越高；越接近-1，表示市场平稳适合做空。",
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
        short_vol = returns.rolling(5).std() * np.sqrt(252)  # 短期年化波动率
        long_vol = returns.rolling(20).std() * np.sqrt(252)  # 长期年化波动率
        ratio = short_vol / long_vol.replace(0, np.nan)  # 避免除零
        ratio = ratio.ffill().fillna(1.0)  # 填充缺失
        # 映射到[-1,1]，使用tanh归一化
        result = np.tanh((ratio - 1.0) * 5)  # 偏离1越多，风险越大
        return pd.Series(result, index=data.index)
