"""AI因子: 价格效率比 | 置信:75% | 衡量价格方向性移动相对于总路径长度的效率。低效率表示高噪声、震荡市场，容易触发持仓超时亏损（如regime=unknown）。高效率（接近+1）表示强趋势，低效率（接近-1）表示无趋势横盘。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceEfficiencyRatio(BaseFactor):
    """衡量价格方向性移动相对于总路径长度的效率。低效率表示高噪声、震荡市场，容易触发持仓超时亏损（如regime=unknown）。高效率（接近+1）表示强趋势，低效率（接近-1）表示无趋势横盘。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_effr",
            name="Price Efficiency Ratio",
            display_name="价格效率比",
            description="衡量价格方向性移动相对于总路径长度的效率。低效率表示高噪声、震荡市场，容易触发持仓超时亏损（如regime=unknown）。高效率（接近+1）表示强趋势，低效率（接近-1）表示无趋势横盘。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        period = 20
        # 价格变化方向
        direction = close.diff(period).abs()
        # 路径长度
        volatility = close.diff().abs().rolling(window=period).sum()
        # 效率比，原始范围[0,1]
        eff_ratio = direction / volatility.replace(0, np.nan)
        eff_ratio = eff_ratio.fillna(0)
        # 映射到[-1, 1]，高效率为+1，低效率为-1
        result = 2 * eff_ratio - 1
        # 限制在[-1,1]
        result = result.clip(-1, 1)
        return result
