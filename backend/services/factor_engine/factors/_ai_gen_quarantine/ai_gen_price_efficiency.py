"""AI因子: 价格效率因子 | 置信:60% | 计算价格位移与总路径长度的比值，衡量趋势推进的效率。效率低（接近0）表示市场充满噪声、反复拉扯，容易触发持仓超时；效率高（接近1）表示方向明确，趋势交易不易超时。因子映射到[-1,1]，低效率为负，高效率为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceEfficiencyFactor(BaseFactor):
    """计算价格位移与总路径长度的比值，衡量趋势推进的效率。效率低（接近0）表示市场充满噪声、反复拉扯，容易触发持仓超时；效率高（接近1）表示方向明确，趋势交易不易超时。因子映射到[-1,1]，低效率为负，高效率为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_efficiency",
            name="Price Efficiency Factor",
            display_name="价格效率因子",
            description="计算价格位移与总路径长度的比值，衡量趋势推进的效率。效率低（接近0）表示市场充满噪声、反复拉扯，容易触发持仓超时；效率高（接近1）表示方向明确，趋势交易不易超时。因子映射到[-1,1]，低效率为负，高效率为正。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        window = 20
        # 价格位移：当前收盘相对于窗口前收盘的变化
        displacement = close.diff(window).abs()
        # 总路径长度：每日绝对收益之和
        path = close.diff().abs().rolling(window).sum()
        # 效率比值，避免除以零
        efficiency = displacement / (path + 1e-10)
        # 将效率(0~1)映射到[-1, 1]，默认0.5映射到0
        result = (efficiency - 0.5) * 2
        return result.fillna(0)
