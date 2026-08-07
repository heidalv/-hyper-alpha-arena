"""AI因子: 价格效率过滤 | 置信:70% | 计算考夫曼效率比，衡量价格运动的趋势效率。低效率比表示价格锯齿震荡，易导致趋势策略持仓超时或止损亏损。信号负值表示低效震荡应过滤，正值表示高效趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class EfficiencyRatioFilter(BaseFactor):
    """计算考夫曼效率比，衡量价格运动的趋势效率。低效率比表示价格锯齿震荡，易导致趋势策略持仓超时或止损亏损。信号负值表示低效震荡应过滤，正值表示高效趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_efficiency_ratio",
            name="Efficiency Ratio Filter",
            display_name="价格效率过滤",
            description="计算考夫曼效率比，衡量价格运动的趋势效率。低效率比表示价格锯齿震荡，易导致趋势策略持仓超时或止损亏损。信号负值表示低效震荡应过滤，正值表示高效趋势。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        period = 14
        # 方向性移动
        direction = close.diff(period).abs()
        # 路径波动总和
        volatility = close.diff().abs().rolling(window=period).sum()
        efficiency_ratio = direction / volatility.replace(0, np.nan)
        # 将效率比映射到[-1,1]，假设中位数0.3为中性
        er_median = efficiency_ratio.rolling(window=100).median()
        er_std = efficiency_ratio.rolling(window=100).std()
        z_score = (efficiency_ratio - er_median) / er_std.replace(0, np.nan)
        result = z_score.clip(-3, 3) / 3.0
        return result
