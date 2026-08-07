"""AI因子: 动量冲突指标 | 置信:70% | 比较短期动量（5日）与中期动量（20日）方向。若方向相反，则预示趋势不稳定，输出负值；方向相同则输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Conflict_Indicator(BaseFactor):
    """比较短期动量（5日）与中期动量（20日）方向。若方向相反，则预示趋势不稳定，输出负值；方向相同则输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momconflict",
            name="Momentum Conflict Indicator",
            display_name="动量冲突指标",
            description="比较短期动量（5日）与中期动量（20日）方向。若方向相反，则预示趋势不稳定，输出负值；方向相同则输出正值。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        short_mom = close.pct_change(periods=5)
        mid_mom = close.pct_change(periods=20)

        # 均以0为阈值判断方向
        short_pos = short_mom > 0
        mid_pos = mid_mom > 0

        # 方向相同：短多长多或短空长空 -> +1；方向相反：短多长空或短空长多 -> -1
        same_direction = (short_pos == mid_pos)
        result = pd.Series(np.where(same_direction, 1.0, -1.0), index=data.index)
        # 处理NaN：若任一动量缺失，返回0
        nan_mask = short_mom.isna() | mid_mom.isna()
        result[nan_mask] = 0.0
        return result
