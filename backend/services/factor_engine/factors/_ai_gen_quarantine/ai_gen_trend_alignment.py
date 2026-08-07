"""AI因子: 多周期趋势一致性 | 置信:60% | 衡量短期(5日)、中期(20日)、长期(60日)动量方向的一致性。当三者同向时信号强(+1或-1)，当混乱时信号接近0。regime unknown时往往方向不一致，该因子可帮助过滤。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Trend_Alignment(BaseFactor):
    """衡量短期(5日)、中期(20日)、长期(60日)动量方向的一致性。当三者同向时信号强(+1或-1)，当混乱时信号接近0。regime unknown时往往方向不一致，该因子可帮助过滤。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_alignment",
            name="Multi-Timeframe Trend Alignment",
            display_name="多周期趋势一致性",
            description="衡量短期(5日)、中期(20日)、长期(60日)动量方向的一致性。当三者同向时信号强(+1或-1)，当混乱时信号接近0。regime unknown时往往方向不一致，该因子可帮助过滤。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 计算不同周期的动量方向（价格变化率的符号）
        mom5 = close.diff(5).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        mom20 = close.diff(20).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        mom60 = close.diff(60).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        # 一致性得分：三个方向之和除以3，范围[-1,1]
        alignment = (mom5 + mom20 + mom60) / 3.0
        return alignment.fillna(0.0)
