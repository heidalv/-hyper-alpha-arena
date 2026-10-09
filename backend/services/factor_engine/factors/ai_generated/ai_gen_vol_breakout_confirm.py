"""AI因子: 放量突破确认 | 置信:58% | 价格短期收益与成交量放量程度的交互：当价格上涨且成交量显著高于中位数时给出强正向信号，缩量上涨则信号减弱。用于确认突破有效性，输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeBreakoutConfirmation(BaseFactor):
    """价格短期收益与成交量放量程度的交互：当价格上涨且成交量显著高于中位数时给出强正向信号，缩量上涨则信号减弱。用于确认突破有效性，输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakout_confirm",
            name="Volume Breakout Confirmation",
            display_name="放量突破确认",
            description="价格短期收益与成交量放量程度的交互：当价格上涨且成交量显著高于中位数时给出强正向信号，缩量上涨则信号减弱。用于确认突破有效性，输出[-1,1]。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_ratio = data['volume'] / (data['volume'].rolling(50).median() + 1e-9)
        result = (ret * (vol_ratio - 1)).clip(-1, 1)
        return result
