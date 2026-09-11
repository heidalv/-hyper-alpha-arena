"""AI因子: 放量波动突破确认 | 置信:58% | 当短期已实现波动率相对长期放大且成交量同步放大时，突破方向更可能延续。用波动率突变比与量能比的乘积乘以短期收益方向，捕捉放量突破确认的动量alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConfirmedVolatilityBreakout(BaseFactor):
    """当短期已实现波动率相对长期放大且成交量同步放大时，突破方向更可能延续。用波动率突变比与量能比的乘积乘以短期收益方向，捕捉放量突破确认的动量alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakout_confirm",
            name="Volume Confirmed Volatility Breakout",
            display_name="放量波动突破确认",
            description="当短期已实现波动率相对长期放大且成交量同步放大时，突破方向更可能延续。用波动率突变比与量能比的乘积乘以短期收益方向，捕捉放量突破确认的动量alpha。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        vol_s = ret.rolling(5).std()
        vol_l = ret.rolling(30).std() + 1e-9
        vol_ratio = vol_s / vol_l
        vol_ma = data['volume'].rolling(5).mean()
        vol_base = data['volume'].rolling(30).mean() + 1e-9
        volu_ratio = vol_ma / vol_base
        mom = data['close'].pct_change(5)
        result = (mom * (vol_ratio - 1) * (volu_ratio - 1)).clip(-1, 1)
        return result
