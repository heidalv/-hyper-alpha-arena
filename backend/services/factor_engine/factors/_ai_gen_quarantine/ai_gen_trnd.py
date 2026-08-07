"""AI因子: 趋势清晰度 | 置信:60% | 通过比较短期（5日）、中期（20日）和长期（60日）移动平均线的排列顺序和发散程度，量化趋势的清晰度。当三线平行且方向一致时，趋势清晰；当交叉或粘合时，趋势模糊，容易导致未知状态下的亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Clarity(BaseFactor):
    """通过比较短期（5日）、中期（20日）和长期（60日）移动平均线的排列顺序和发散程度，量化趋势的清晰度。当三线平行且方向一致时，趋势清晰；当交叉或粘合时，趋势模糊，容易导致未知状态下的亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trnd",
            name="Trend Clarity",
            display_name="趋势清晰度",
            description="通过比较短期（5日）、中期（20日）和长期（60日）移动平均线的排列顺序和发散程度，量化趋势的清晰度。当三线平行且方向一致时，趋势清晰；当交叉或粘合时，趋势模糊，容易导致未知状态下的亏损。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        # 计算距离：短期相对中期，中期相对长期
        dist1 = (ma5 - ma20) / (ma20 + 1e-10)
        dist2 = (ma20 - ma60) / (ma60 + 1e-10)
        # 方向一致性：同号且绝对值大则清晰
        alignment = np.sign(dist1) * np.sign(dist2)
        magnitude = np.abs(dist1) + np.abs(dist2)
        # 清晰度 = alignment * magnitude，再归一化
        clarity = alignment * magnitude
        # 平滑和标准化
        clarity = clarity.rolling(5).mean()
        result = pd.Series(np.tanh(clarity * 10), index=data.index)
        return result
