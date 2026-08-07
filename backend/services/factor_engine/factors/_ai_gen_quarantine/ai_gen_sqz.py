"""AI因子: 波动率挤压 | 置信:70% | 衡量当前波动率在历史中的相对水平，波动率极度收缩（挤压）时市场积蓄力量但方向不明，亏损常发生在此时持仓超时。值接近-1表示挤压（不宜交易），接近+1表示波动扩张（方向明确）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySqueeze(BaseFactor):
    """衡量当前波动率在历史中的相对水平，波动率极度收缩（挤压）时市场积蓄力量但方向不明，亏损常发生在此时持仓超时。值接近-1表示挤压（不宜交易），接近+1表示波动扩张（方向明确）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sqz",
            name="Volatility Squeeze",
            display_name="波动率挤压",
            description="衡量当前波动率在历史中的相对水平，波动率极度收缩（挤压）时市场积蓄力量但方向不明，亏损常发生在此时持仓超时。值接近-1表示挤压（不宜交易），接近+1表示波动扩张（方向明确）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        N = 20
        M = 200
        bb_width = close.rolling(N).std() * 2
        # 宽度在长期中的百分位排名
        rank = bb_width.rolling(M).rank(pct=True)
        # 挤压时宽度小 -> rank小 -> 值接近-1
        result = 2 * rank - 1
        return result.fillna(0).clip(-1, 1)
