"""AI因子: 波动调整收盘位置 | 置信:60% | 收盘价在当日高低区间中的相对位置(收盘位置)反映买方/卖方当日掌控力，用20期已实现波动率做标准化。高位收盘且低波动代表稳健买盘，低位收盘代表卖压，因子值越高预示未来上涨概率越高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionVolatilityAdjusted(BaseFactor):
    """收盘价在当日高低区间中的相对位置(收盘位置)反映买方/卖方当日掌控力，用20期已实现波动率做标准化。高位收盘且低波动代表稳健买盘，低位收盘代表卖压，因子值越高预示未来上涨概率越高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_position_vol_adjusted",
            name="Close Position Volatility Adjusted",
            display_name="波动调整收盘位置",
            description="收盘价在当日高低区间中的相对位置(收盘位置)反映买方/卖方当日掌控力，用20期已实现波动率做标准化。高位收盘且低波动代表稳健买盘，低位收盘代表卖压，因子值越高预示未来上涨概率越高。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = data['high'] - data['low'] + 1e-9
        pos = (data['close'] - data['low']) / rng
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((pos - 0.5) / vol).rolling(3).mean().clip(-1, 1)
        return result
