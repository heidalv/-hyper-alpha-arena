"""AI因子: 止损距离动量比 | 置信:50% | 针对sl止损亏损模式：亏损常因入场后价格快速反向突破止损。用近期价格相对高低点位置与成交量变化率，捕捉高波动+放量反向的止损触发风险，值越低越危险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stoplossdistanceratio(BaseFactor):
    """针对sl止损亏损模式：亏损常因入场后价格快速反向突破止损。用近期价格相对高低点位置与成交量变化率，捕捉高波动+放量反向的止损触发风险，值越低越危险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stop_loss_ratio",
            name="StopLossDistanceRatio",
            display_name="止损距离动量比",
            description="针对sl止损亏损模式：亏损常因入场后价格快速反向突破止损。用近期价格相对高低点位置与成交量变化率，捕捉高波动+放量反向的止损触发风险，值越低越危险。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_roll = data['high'].rolling(10).max()
        low_roll = data['low'].rolling(10).min()
        mid = (high_roll + low_roll) / 2
        pos = (data['close'] - mid) / (high_roll - low_roll + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (pos * 2 - 1) * (vol_ratio - 1)
        result = (result / (result.abs().rolling(20).max() + 1e-9)).clip(-1, 1)
        return result.fillna(0)
