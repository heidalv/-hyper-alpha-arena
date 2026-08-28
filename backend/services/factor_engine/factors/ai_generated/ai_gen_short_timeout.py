"""AI因子: 空头超时压力 | 置信:55% | 识别空头持仓超时导致的亏损模式：当价格处于短期均线下方但近期波动收窄，且成交量萎缩时，空头容易因长时间无进展而被迫平仓。该因子通过价格相对位置与波动收缩的交互捕捉此状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Shorttimeoutpressure(BaseFactor):
    """识别空头持仓超时导致的亏损模式：当价格处于短期均线下方但近期波动收窄，且成交量萎缩时，空头容易因长时间无进展而被迫平仓。该因子通过价格相对位置与波动收缩的交互捕捉此状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_timeout",
            name="ShortTimeoutPressure",
            display_name="空头超时压力",
            description="识别空头持仓超时导致的亏损模式：当价格处于短期均线下方但近期波动收窄，且成交量萎缩时，空头容易因长时间无进展而被迫平仓。该因子通过价格相对位置与波动收缩的交互捕捉此状态。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol = data['close'].pct_change().rolling(10).std()
        ma = data['close'].rolling(20).mean()
        pos = (data['close'] - ma) / (data['close'].rolling(20).std() + 1e-9)
        vol_ratio = vol / (data['close'].pct_change().rolling(50).std() + 1e-9)
        result = (-pos * (1 - vol_ratio).clip(0, 1)).clip(-1, 1)
        return result
