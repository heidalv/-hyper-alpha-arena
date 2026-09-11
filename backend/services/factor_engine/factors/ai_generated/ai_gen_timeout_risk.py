"""AI因子: 持仓超时风险溢价 | 置信:55% | 基于亏损模式中max_hold_timeout频繁出现，捕捉价格在持仓周期内未达预期时的衰减风险。使用长周期收益与波动率比值，识别趋势衰竭信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutRiskPremium(BaseFactor):
    """基于亏损模式中max_hold_timeout频繁出现，捕捉价格在持仓周期内未达预期时的衰减风险。使用长周期收益与波动率比值，识别趋势衰竭信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_risk",
            name="Timeout Risk Premium",
            display_name="持仓超时风险溢价",
            description="基于亏损模式中max_hold_timeout频繁出现，捕捉价格在持仓周期内未达预期时的衰减风险。使用长周期收益与波动率比值，识别趋势衰竭信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(48)
        vol = data['close'].pct_change().rolling(48).std()
        result = (ret / (vol + 1e-9)).clip(-1, 1)
        return result
