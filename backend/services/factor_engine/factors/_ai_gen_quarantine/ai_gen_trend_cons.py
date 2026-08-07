"""AI因子: 趋势一致性 | 置信:60% | 计算短期动量(3日)与长期动量(20日)的方向一致性。若两者方向相反或均较弱，则趋势不明确，输出负值；若方向一致且强劲，输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConsistency(BaseFactor):
    """计算短期动量(3日)与长期动量(20日)的方向一致性。若两者方向相反或均较弱，则趋势不明确，输出负值；若方向一致且强劲，输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_cons",
            name="Trend Consistency",
            display_name="趋势一致性",
            description="计算短期动量(3日)与长期动量(20日)的方向一致性。若两者方向相反或均较弱，则趋势不明确，输出负值；若方向一致且强劲，输出正值。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        mom_short = close.pct_change(3)
        mom_long = close.pct_change(20)
        # 方向一致度：两者乘积的正负和大小
        consistency = mom_short * mom_long
        # 归一化到[-1,1] 用tanh
        result = np.tanh(consistency * 10)
        return result.fillna(0)
