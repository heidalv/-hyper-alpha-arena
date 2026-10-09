"""AI因子: 量能高潮反转 | 置信:60% | 捕捉成交量极端放大后的价格反转。当成交量远超近期中位数且价格出现明显趋势时，预示短期情绪过热，反向操作。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeClimaxReversal(BaseFactor):
    """捕捉成交量极端放大后的价格反转。当成交量远超近期中位数且价格出现明显趋势时，预示短期情绪过热，反向操作。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_climax",
            name="Volume_Climax_Reversal",
            display_name="量能高潮反转",
            description="捕捉成交量极端放大后的价格反转。当成交量远超近期中位数且价格出现明显趋势时，预示短期情绪过热，反向操作。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_med = data['volume'].rolling(20).median()
        vol_ratio = data['volume'] / (vol_med + 1e-9)
        ret_1 = data['close'].pct_change(1)
        ret_5 = data['close'].pct_change(5)
        signal = (vol_ratio > 2).astype(float) * (-ret_1 * 0.7 + ret_5 * 0.3)
        result = signal * (vol_ratio / 5).clip(0, 1)
        return result.clip(-1, 1)
