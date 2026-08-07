"""AI因子: 趋势一致性因子 | 置信:60% | 计算短期、中期、长期移动平均线之间的偏差，综合判断趋势是否明确。正值表示上升趋势一致，负值表示下降趋势一致，接近0表示均线缠绕（无趋势），对应未知市场状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConsensus(BaseFactor):
    """计算短期、中期、长期移动平均线之间的偏差，综合判断趋势是否明确。正值表示上升趋势一致，负值表示下降趋势一致，接近0表示均线缠绕（无趋势），对应未知市场状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_consensus",
            name="trend_consensus",
            display_name="趋势一致性因子",
            description="计算短期、中期、长期移动平均线之间的偏差，综合判断趋势是否明确。正值表示上升趋势一致，负值表示下降趋势一致，接近0表示均线缠绕（无趋势），对应未知市场状态。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        # 短期与中期偏离
        diff1 = (ma5 - ma20) / (close + 1e-10)
        # 中期与长期偏离
        diff2 = (ma20 - ma60) / (close + 1e-10)
        # 综合偏离，用tanh压缩到[-1,1]
        result = np.tanh(diff1 + diff2)
        return result.fillna(0)
