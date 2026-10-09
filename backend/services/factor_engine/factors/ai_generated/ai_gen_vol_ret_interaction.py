"""AI因子: 波动率收益交互 | 置信:58% | 短期收益除以已实现波动率，衡量单位风险下的动量强度。高波动环境下收益被放大时更易反转，低波动下趋势更稳。用波动率突变(短/长波动比)对收益方向做加权，捕捉波动聚集下的收益可预测性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityReturnInteraction(BaseFactor):
    """短期收益除以已实现波动率，衡量单位风险下的动量强度。高波动环境下收益被放大时更易反转，低波动下趋势更稳。用波动率突变(短/长波动比)对收益方向做加权，捕捉波动聚集下的收益可预测性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_ret_interaction",
            name="Volatility Return Interaction",
            display_name="波动率收益交互",
            description="短期收益除以已实现波动率，衡量单位风险下的动量强度。高波动环境下收益被放大时更易反转，低波动下趋势更稳。用波动率突变(短/长波动比)对收益方向做加权，捕捉波动聚集下的收益可预测性。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret5 = data['close'].pct_change(5)
        vol20 = data['close'].pct_change().rolling(20).std() + 1e-9
        vol5 = data['close'].pct_change().rolling(5).std() + 1e-9
        result = ((ret5 / vol20) * (vol5 / vol20)).clip(-1, 1)
        return result
