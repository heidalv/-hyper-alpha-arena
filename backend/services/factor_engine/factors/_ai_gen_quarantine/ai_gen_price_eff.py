"""AI因子: 价格效率比因子 | 置信:70% | 计算过去20日的价格净变动与总波动的比值（效率比）。低效率比意味着价格震荡剧烈、方向性弱，代表典型的不确定状态（regime unknown），做多容易受假突破亏损，因子输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Efficiency_Ratio_Factor(BaseFactor):
    """计算过去20日的价格净变动与总波动的比值（效率比）。低效率比意味着价格震荡剧烈、方向性弱，代表典型的不确定状态（regime unknown），做多容易受假突破亏损，因子输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_eff",
            name="Price Efficiency Ratio Factor",
            display_name="价格效率比因子",
            description="计算过去20日的价格净变动与总波动的比值（效率比）。低效率比意味着价格震荡剧烈、方向性弱，代表典型的不确定状态（regime unknown），做多容易受假突破亏损，因子输出负值。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        length = len(data)
        if length < 20:
            return pd.Series(np.nan, index=data.index)
        close = data['close']
        net_change = close - close.shift(20)
        abs_returns = close.diff().abs().rolling(20).sum()
        efficiency = net_change / abs_returns
        efficiency = efficiency.clip(-1, 1)
        # 取绝对值后取负，低效率（接近0）时输出负值
        result = -(1 - np.abs(efficiency)) * 2 + 1  # 映射：|eff|=1 => +1, |eff|=0 => -1
        result = result.clip(-1, 1)
        return result.fillna(0)
