"""AI因子: 效率比率 | 置信:60% | 通过计算N天内价格变化绝对值与逐日价格变化绝对值之和的比值，衡量市场趋势性。低ER（震荡行情）返回负值，高ER（趋势行情）返回正值，用于识别不适合做多的未知市场状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Efficiency_Ratio(BaseFactor):
    """通过计算N天内价格变化绝对值与逐日价格变化绝对值之和的比值，衡量市场趋势性。低ER（震荡行情）返回负值，高ER（趋势行情）返回正值，用于识别不适合做多的未知市场状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_er",
            name="Efficiency Ratio",
            display_name="效率比率",
            description="通过计算N天内价格变化绝对值与逐日价格变化绝对值之和的比值，衡量市场趋势性。低ER（震荡行情）返回负值，高ER（趋势行情）返回正值，用于识别不适合做多的未知市场状态。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        period = 14
        close = data['close']
        # 价格变化绝对值
        price_move = close.diff(period).abs()
        # 逐日变化绝对值之和
        daily_move = close.diff().abs().rolling(period).sum()
        er = price_move / daily_move
        # 映射到[-1,1]
        result = er * 2 - 1
        return result
