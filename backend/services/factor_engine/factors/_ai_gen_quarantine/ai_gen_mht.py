"""AI因子: 持仓超时风险 | 置信:50% | 衡量持仓时间过长但趋势未延续的风险。通过计算价格与短期均线的偏离度以及价格变化的持续性，当价格在均线附近徘徊且波动率下降时，容易导致持仓超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MaxHoldTimeoutRisk(BaseFactor):
    """衡量持仓时间过长但趋势未延续的风险。通过计算价格与短期均线的偏离度以及价格变化的持续性，当价格在均线附近徘徊且波动率下降时，容易导致持仓超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mht",
            name="MaxHoldTimeoutRisk",
            display_name="持仓超时风险",
            description="衡量持仓时间过长但趋势未延续的风险。通过计算价格与短期均线的偏离度以及价格变化的持续性，当价格在均线附近徘徊且波动率下降时，容易导致持仓超时亏损。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 计算价格相对20周期均线的位置
        ma20 = close.rolling(20).mean()
        deviation = (close - ma20) / ma20
        # 计算短期价格变化幅度（3周期）
        change = close.pct_change(3)
        # 当偏离度小且变化幅度小时，持仓超时风险高
        raw = -abs(deviation) * (1 - abs(change).rolling(10).mean())
        # 标准化
        norm = (raw - raw.rolling(30).mean()) / (raw.rolling(30).std() + 1e-8)
        return norm.clip(-1, 1)
