"""AI因子: 量价背离因子 | 置信:60% | 衡量价格变动方向与成交量变动方向的一致性。正值表示量价同步，趋势健康；负值表示量价背离，趋势动能衰竭，易反转，可预防因动能不足导致的超时持有或止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """衡量价格变动方向与成交量变动方向的一致性。正值表示量价同步，趋势健康；负值表示量价背离，趋势动能衰竭，易反转，可预防因动能不足导致的超时持有或止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vp_diverge",
            name="Volume-Price Divergence",
            display_name="量价背离因子",
            description="衡量价格变动方向与成交量变动方向的一致性。正值表示量价同步，趋势健康；负值表示量价背离，趋势动能衰竭，易反转，可预防因动能不足导致的超时持有或止损。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        period = 14
        price_dir = (close.diff(period) >= 0).astype(int) * 2 - 1
        vol_dir = (volume.diff(period) >= 0).astype(int) * 2 - 1
        sync = (price_dir == vol_dir).astype(int) * 2 - 1
        result = sync.rolling(period, min_periods=1).mean()
        return result.clip(-1, 1)
