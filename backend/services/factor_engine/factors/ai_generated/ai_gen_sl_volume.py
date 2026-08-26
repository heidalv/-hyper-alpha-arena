"""AI因子: 止损放量异动 | 置信:58% | 捕捉sl止损亏损模式：在成交量异常放大时，价格容易快速反向突破止损。使用成交量相对20日中位数的倍数与短期价格反转强度的组合，识别放量后的趋势衰竭点。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StoplossVolumeSpike(BaseFactor):
    """捕捉sl止损亏损模式：在成交量异常放大时，价格容易快速反向突破止损。使用成交量相对20日中位数的倍数与短期价格反转强度的组合，识别放量后的趋势衰竭点。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_volume",
            name="StopLoss_Volume_Spike",
            display_name="止损放量异动",
            description="捕捉sl止损亏损模式：在成交量异常放大时，价格容易快速反向突破止损。使用成交量相对20日中位数的倍数与短期价格反转强度的组合，识别放量后的趋势衰竭点。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_med = data['volume'].rolling(20).median()
        vol_ratio = data['volume'] / (vol_med + 1e-9)
        ret_short = data['close'].pct_change(3)
        ret_long = data['close'].pct_change(10)
        reversal = (ret_short - ret_long).abs()
        result = (vol_ratio * reversal * 0.5).clip(-1, 1)
        return result.fillna(0)
