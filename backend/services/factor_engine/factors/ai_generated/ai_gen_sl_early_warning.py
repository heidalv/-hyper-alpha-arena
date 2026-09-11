"""AI因子: 止损预警因子 | 置信:55% | 针对多个止损亏损案例，通过价格加速下跌和成交量放大识别高风险开仓点。当短期跌幅过大且量能激增时，因子值趋向-1，警示追空风险；当价格平稳或反弹时，因子值趋向+1，适合观望或反向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stoplossearlywarning(BaseFactor):
    """针对多个止损亏损案例，通过价格加速下跌和成交量放大识别高风险开仓点。当短期跌幅过大且量能激增时，因子值趋向-1，警示追空风险；当价格平稳或反弹时，因子值趋向+1，适合观望或反向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_early_warning",
            name="StopLossEarlyWarning",
            display_name="止损预警因子",
            description="针对多个止损亏损案例，通过价格加速下跌和成交量放大识别高风险开仓点。当短期跌幅过大且量能激增时，因子值趋向-1，警示追空风险；当价格平稳或反弹时，因子值趋向+1，适合观望或反向。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret_3 = data['close'].pct_change(3)
        ret_1 = data['close'].pct_change(1)
        accel = ret_1 - ret_3 / 3
        vol_ratio = data['volume'].rolling(3).mean() / (data['volume'].rolling(10).mean() + 1e-9)
        result = (accel * -2 + vol_ratio * -0.5).clip(-1, 1)
        return result
