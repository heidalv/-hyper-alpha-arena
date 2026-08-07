"""AI因子: 成交量确认背离 | 置信:60% | 衡量价格变化方向与成交量变化方向的一致性。正值为量价配合（趋势可靠），负值为背离（上涨缩量或下跌放量后缩量），提前预警因趋势乏力导致的持仓超时或手动止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConfirmationDivergence(BaseFactor):
    """衡量价格变化方向与成交量变化方向的一致性。正值为量价配合（趋势可靠），负值为背离（上涨缩量或下跌放量后缩量），提前预警因趋势乏力导致的持仓超时或手动止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vcd",
            name="Volume Confirmation Divergence",
            display_name="成交量确认背离",
            description="衡量价格变化方向与成交量变化方向的一致性。正值为量价配合（趋势可靠），负值为背离（上涨缩量或下跌放量后缩量），提前预警因趋势乏力导致的持仓超时或手动止损。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 价格变化方向
        price_diff = close.diff(5)
        # 成交量变化
        vol_diff = volume.diff(5)
        # 平滑处理
        smooth_price = price_diff.ewm(span=3).mean()
        smooth_vol = vol_diff.ewm(span=3).mean()
        # 标准化
        price_z = (smooth_price - smooth_price.rolling(30).mean()) / (smooth_price.rolling(30).std() + 1e-9)
        vol_z = (smooth_vol - smooth_vol.rolling(30).mean()) / (smooth_vol.rolling(30).std() + 1e-9)
        # 量价确认度：同向为正，异向为负
        confirmation = price_z * vol_z
        result = np.tanh(confirmation)
        return result.clip(-1, 1)
