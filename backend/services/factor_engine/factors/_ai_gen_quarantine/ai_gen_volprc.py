"""AI因子: 量价背离 | 置信:60% | 计算最近N日价格变化与成交量变化的相关性，取负值。正相关（同向）表示趋势健康，负相关（背离）暗示趋势衰竭或反转风险，尤其在regime unknown时。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """计算最近N日价格变化与成交量变化的相关性，取负值。正相关（同向）表示趋势健康，负相关（背离）暗示趋势衰竭或反转风险，尤其在regime unknown时。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprc",
            name="Volume-Price Divergence",
            display_name="量价背离",
            description="计算最近N日价格变化与成交量变化的相关性，取负值。正相关（同向）表示趋势健康，负相关（背离）暗示趋势衰竭或反转风险，尤其在regime unknown时。",
            category="composite",
            subcategory="behavioral",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        window = 10
        # 价格变化率
        ret = data['close'].pct_change()
        # 成交量变化率
        vol_ret = data['volume'].pct_change()
        # 计算滚动相关系数
        corr = ret.rolling(window).corr(vol_ret)
        # 取负号，使背离为正值（负相关时因子>0）
        result = -corr
        # 填充缺失值为0（中性）
        result = result.fillna(0.0)
        # 确保值域在[-1,1]（corr本身在[-1,1]）
        return result
