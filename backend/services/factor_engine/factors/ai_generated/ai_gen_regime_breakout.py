"""AI因子: 未知状态突破反转 | 置信:58% | regime=unknown时，价格突破往往缺乏持续性，容易在超时或止损中亏损。该因子识别高波动环境下的假突破，通过价格与通道位置及成交量变化的反向关系捕捉反转机会。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUnknownBreakoutFade(BaseFactor):
    """regime=unknown时，价格突破往往缺乏持续性，容易在超时或止损中亏损。该因子识别高波动环境下的假突破，通过价格与通道位置及成交量变化的反向关系捕捉反转机会。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_breakout",
            name="Regime Unknown Breakout Fade",
            display_name="未知状态突破反转",
            description="regime=unknown时，价格突破往往缺乏持续性，容易在超时或止损中亏损。该因子识别高波动环境下的假突破，通过价格与通道位置及成交量变化的反向关系捕捉反转机会。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        pos = (data['close'] - low_20) / (high_20 - low_20 + 1e-9)
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        result = (0.5 - pos) * (vol_ratio - 1).clip(-1, 1)
        result = result.fillna(0)
        return result.clip(-1, 1)
