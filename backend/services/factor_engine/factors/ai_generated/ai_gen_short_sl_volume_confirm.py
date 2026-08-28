"""AI因子: 做空止损成交量确认 | 置信:50% | 针对做空单sl亏损模式：做空止损往往发生在价格反弹且成交量放大时。该因子检测价格从低位反弹伴随成交量放大的情况，值越接近+1表示反弹动能越强（做空风险高），越接近-1表示下跌动能持续（做空相对安全）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortStoplossVolumeConfirmation(BaseFactor):
    """针对做空单sl亏损模式：做空止损往往发生在价格反弹且成交量放大时。该因子检测价格从低位反弹伴随成交量放大的情况，值越接近+1表示反弹动能越强（做空风险高），越接近-1表示下跌动能持续（做空相对安全）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_sl_volume_confirm",
            name="Short_StopLoss_Volume_Confirmation",
            display_name="做空止损成交量确认",
            description="针对做空单sl亏损模式：做空止损往往发生在价格反弹且成交量放大时。该因子检测价格从低位反弹伴随成交量放大的情况，值越接近+1表示反弹动能越强（做空风险高），越接近-1表示下跌动能持续（做空相对安全）。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(3)
        vol_surge = data['volume'] / (data['volume'].rolling(10).mean() + 1e-9)
        low_dist = (data['close'] - data['low'].rolling(5).min()) / (data['close'] + 1e-9)
        result = (ret * 2 + (vol_surge - 1) - low_dist).clip(-1, 1)
        return result
