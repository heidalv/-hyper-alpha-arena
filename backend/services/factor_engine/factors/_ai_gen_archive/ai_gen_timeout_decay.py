"""AI因子: 持仓超时衰减 | 置信:50% | 针对max_hold_timeout在多个品种上亏损，用价格动量衰减与成交量萎缩识别持仓失效场景，值越高表示越应提前离场。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutDecay(BaseFactor):
    """针对max_hold_timeout在多个品种上亏损，用价格动量衰减与成交量萎缩识别持仓失效场景，值越高表示越应提前离场。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_decay",
            name="Hold Timeout Decay",
            display_name="持仓超时衰减",
            description="针对max_hold_timeout在多个品种上亏损，用价格动量衰减与成交量萎缩识别持仓失效场景，值越高表示越应提前离场。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret_short = data['close'].pct_change(3)
        ret_long = data['close'].pct_change(10)
        momentum_diff = ret_short - ret_long
        vol_short = data['close'].pct_change().rolling(5).std()
        vol_long = data['close'].pct_change().rolling(20).std()
        vol_ratio = vol_short / (vol_long + 1e-9)
        volume_ratio = data['volume'] / (data['volume'].rolling(30).mean() + 1e-9)
        result = -momentum_diff * 0.4 + (vol_ratio - 1).clip(-1, 1) * 0.3 + (1 - volume_ratio).clip(-1, 1) * 0.3
        result = result.clip(-1, 1)
        return result.fillna(0.0)
