"""AI因子: 均线排列混乱 | 置信:70% | 短期、中期、长期均线相互交错，无清晰趋势方向，此时入场容易遭遇假突破。因子计算三条均线（5,20,60）两两之间的排序一致性，完全一致为1，完全混乱为-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Moving_Average_Disorder(BaseFactor):
    """短期、中期、长期均线相互交错，无清晰趋势方向，此时入场容易遭遇假突破。因子计算三条均线（5,20,60）两两之间的排序一致性，完全一致为1，完全混乱为-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mad",
            name="Moving_Average_Disorder",
            display_name="均线排列混乱",
            description="短期、中期、长期均线相互交错，无清晰趋势方向，此时入场容易遭遇假突破。因子计算三条均线（5,20,60）两两之间的排序一致性，完全一致为1，完全混乱为-1。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import pandas as pd
        import numpy as np
        close = data['close']
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        # 判断均线顺序：多头排列 ma5>ma20>ma60 记1，空头排列 -1，否则0
        order = ((ma5 > ma20) & (ma20 > ma60)).astype(float) - ((ma5 < ma20) & (ma20 < ma60)).astype(float)
        # 计算均线间距离的变异系数衡量混乱程度
        stack = pd.concat([ma5, ma20, ma60], axis=1)
        mean = stack.mean(axis=1)
        std = stack.std(axis=1)
        cv = std / (mean + 1e-10)
        # 混乱度加权的方向因子
        factor = order * (1 - cv.clip(0, 1))
        factor = factor.fillna(0).clip(-1, 1)
        return factor
