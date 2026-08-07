"""AI因子: 残差均值回归 | 置信:60% | 使用简单移动平均线（20日）提取趋势，计算价格与趋势的残差。当残差超过2个标准差时，预测价格回归趋势线。残差为正过大时做空（-1），为负过大时做多（+1），其余为0。适用于无趋势（regime=unknown）的振荡行情。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Residual_Mean_Reversion(BaseFactor):
    """使用简单移动平均线（20日）提取趋势，计算价格与趋势的残差。当残差超过2个标准差时，预测价格回归趋势线。残差为正过大时做空（-1），为负过大时做多（+1），其余为0。适用于无趋势（regime=unknown）的振荡行情。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_residual_meanrev",
            name="Residual Mean Reversion",
            display_name="残差均值回归",
            description="使用简单移动平均线（20日）提取趋势，计算价格与趋势的残差。当残差超过2个标准差时，预测价格回归趋势线。残差为正过大时做空（-1），为负过大时做多（+1），其余为0。适用于无趋势（regime=unknown）的振荡行情。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma = close.rolling(20).mean()
        residual = close - ma
        std = residual.rolling(20).std().replace(0, 1e-10)
        z = residual / std
        result = pd.Series(0.0, index=data.index)
        # 超过2个标准差反向交易
        result[z > 2.0] = -1.0
        result[z < -2.0] = 1.0
        return result
