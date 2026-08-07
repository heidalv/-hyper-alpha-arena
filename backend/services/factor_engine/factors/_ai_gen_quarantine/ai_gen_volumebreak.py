"""AI因子: 量价背离因子 | 置信:55% | 当成交量异常放大但价格未有效突破均线时，视为假突破或流动性陷阱，这种环境下持有头寸容易遭受逆袭。因子通过比较当日成交量与过去20日均值以及价格相对于VWAP的位置，值域[-1,1]，负值表示量增价平或下跌，应规避。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeBreakoutWithoutPriceConfirmation(BaseFactor):
    """当成交量异常放大但价格未有效突破均线时，视为假突破或流动性陷阱，这种环境下持有头寸容易遭受逆袭。因子通过比较当日成交量与过去20日均值以及价格相对于VWAP的位置，值域[-1,1]，负值表示量增价平或下跌，应规避。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volumebreak",
            name="Volume Breakout Without Price Confirmation",
            display_name="量价背离因子",
            description="当成交量异常放大但价格未有效突破均线时，视为假突破或流动性陷阱，这种环境下持有头寸容易遭受逆袭。因子通过比较当日成交量与过去20日均值以及价格相对于VWAP的位置，值域[-1,1]，负值表示量增价平或下跌，应规避。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        vwap = (data['volume'] * (data['high'] + data['low'] + data['close']) / 3).rolling(5).mean() / data['volume'].rolling(5).mean()
        price = (data['high'] + data['low'] + data['close']) / 3
        z_price = (price - vwap) / (price.rolling(20).std() + 1e-8)
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        raw = -vol_ratio * np.sign(z_price) * np.clip(abs(z_price), 0, 2)
        result = np.clip(raw, -1, 1)
        return result.fillna(0)
