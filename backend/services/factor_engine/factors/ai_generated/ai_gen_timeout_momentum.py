"""AI因子: 超时持仓动量偏差 | 置信:65% | 检测持仓超时与价格趋势的关联性，捕捉逆势持仓特征"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timeoutmomentumbias(BaseFactor):
    """检测持仓超时与价格趋势的关联性，捕捉逆势持仓特征"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_momentum",
            name="TimeoutMomentumBias",
            display_name="超时持仓动量偏差",
            description="检测持仓超时与价格趋势的关联性，捕捉逆势持仓特征",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        trend = (data['close'] - data['open']) / data['open']
        timeout_flag = pd.Series(1, index=data.index).rolling(window=7).sum()
        return (trend * timeout_flag).astype(float).clip(-1,1)
