"""AI因子: 放量突破确认 | 置信:58% | 价格突破近期高点且成交量显著放大时，趋势延续概率高；若突破但缩量则视为假突破。用收盘价相对滚动高点的位置乘以成交量相对中位数的放大倍数，构建量价共振因子，输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeBreakoutConfirmation(BaseFactor):
    """价格突破近期高点且成交量显著放大时，趋势延续概率高；若突破但缩量则视为假突破。用收盘价相对滚动高点的位置乘以成交量相对中位数的放大倍数，构建量价共振因子，输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakout_confirm",
            name="Volume Breakout Confirmation",
            display_name="放量突破确认",
            description="价格突破近期高点且成交量显著放大时，趋势延续概率高；若突破但缩量则视为假突破。用收盘价相对滚动高点的位置乘以成交量相对中位数的放大倍数，构建量价共振因子，输出[-1,1]。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high20 = data['close'].rolling(20).max()
        low20 = data['close'].rolling(20).min()
        pos = (data['close'] - low20) / (high20 - low20 + 1e-9)
        volratio = data['volume'] / (data['volume'].rolling(50).median() + 1e-9)
        mom = data['close'].pct_change(10)
        result = ((pos - 0.5) * 2 * (volratio - 1) * (mom / (data['close'].pct_change().rolling(20).std() + 1e-9))).clip(-1, 1)
        return result
