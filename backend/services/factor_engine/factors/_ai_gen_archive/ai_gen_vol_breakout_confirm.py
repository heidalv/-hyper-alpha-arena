"""AI因子: 放量波动突破确认 | 置信:58% | 价格偏离20日均值程度乘以成交量相对50日中位数的放大倍数，放量突破时因子值大，缩量整理时接近0，捕捉量价共振的方向性alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConfirmedVolatilityBreakout(BaseFactor):
    """价格偏离20日均值程度乘以成交量相对50日中位数的放大倍数，放量突破时因子值大，缩量整理时接近0，捕捉量价共振的方向性alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakout_confirm",
            name="Volume Confirmed Volatility Breakout",
            display_name="放量波动突破确认",
            description="价格偏离20日均值程度乘以成交量相对50日中位数的放大倍数，放量突破时因子值大，缩量整理时接近0，捕捉量价共振的方向性alpha。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma = data['close'].rolling(20).mean()
        dev = (data['close'] - ma) / (ma + 1e-9)
        vratio = data['volume'] / (data['volume'].rolling(50).median() + 1e-9)
        result = (dev * (vratio - 1)).clip(-1, 1)
        return result
