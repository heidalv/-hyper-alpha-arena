"""AI因子: 布林带位置 | 置信:65% | 计算价格在布林带中的相对位置。价格高于中轨时看空（因子负值），低于中轨时看多（因子正值），极端位置接近±1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Band_Position(BaseFactor):
    """计算价格在布林带中的相对位置。价格高于中轨时看空（因子负值），低于中轨时看多（因子正值），极端位置接近±1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bb_pos",
            name="Bollinger Band Position",
            display_name="布林带位置",
            description="计算价格在布林带中的相对位置。价格高于中轨时看空（因子负值），低于中轨时看多（因子正值），极端位置接近±1。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 20
        std_mult = 2
        close = data['close']
        middle = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = middle + std_mult * std
        lower = middle - std_mult * std
        # 价格相对位置： (middle - price) / (upper - middle) * 1  -> 上轨处-1，下轨处+1
        width = upper - middle
        result = (middle - close) / width.replace(0, np.nan)
        result = result.clip(-1, 1)
        return result.fillna(0)
