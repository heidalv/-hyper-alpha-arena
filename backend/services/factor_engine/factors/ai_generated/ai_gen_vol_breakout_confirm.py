"""AI因子: 放量突破动量确认 | 置信:58% | 当短期收益为正且成交量显著高于中期均量时，视为有效突破，因子取正；收益为负且放量则视为有效破位，因子取负。通过量价共振过滤假突破，捕捉趋势延续 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConfirmedBreakoutMomentum(BaseFactor):
    """当短期收益为正且成交量显著高于中期均量时，视为有效突破，因子取正；收益为负且放量则视为有效破位，因子取负。通过量价共振过滤假突破，捕捉趋势延续 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakout_confirm",
            name="Volume Confirmed Breakout Momentum",
            display_name="放量突破动量确认",
            description="当短期收益为正且成交量显著高于中期均量时，视为有效突破，因子取正；收益为负且放量则视为有效破位，因子取负。通过量价共振过滤假突破，捕捉趋势延续 alpha。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_ratio = data['volume'].rolling(5).mean() / (data['volume'].rolling(30).mean() + 1e-9)
        result = (ret * 20 * (vol_ratio - 1)).clip(-1, 1)
        return result
