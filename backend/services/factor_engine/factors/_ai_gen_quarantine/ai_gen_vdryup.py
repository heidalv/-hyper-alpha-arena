"""AI因子: 成交量枯竭 | 置信:60% | 综合衡量成交量与价格振幅的萎缩程度。当成交量和日内振幅同步低于历史均值时，市场活性下降，趋势难以持续，持仓容易超时。负值表示枯竭程度高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeAndRangeDryUp(BaseFactor):
    """综合衡量成交量与价格振幅的萎缩程度。当成交量和日内振幅同步低于历史均值时，市场活性下降，趋势难以持续，持仓容易超时。负值表示枯竭程度高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vdryup",
            name="Volume and Range Dry-Up",
            display_name="成交量枯竭",
            description="综合衡量成交量与价格振幅的萎缩程度。当成交量和日内振幅同步低于历史均值时，市场活性下降，趋势难以持续，持仓容易超时。负值表示枯竭程度高。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        volume = data['volume']
        high = data['high']
        low = data['low']
        close = data['close']
        range_pct = (high - low) / close
        vol_mean = volume.rolling(50, min_periods=20).mean()
        vol_std = volume.rolling(50, min_periods=20).std()
        range_mean = range_pct.rolling(50, min_periods=20).mean()
        range_std = range_pct.rolling(50, min_periods=20).std()
        vol_z = (volume - vol_mean) / vol_std
        range_z = (range_pct - range_mean) / range_std
        dryup = (vol_z + range_z) / 2.0
        result = -dryup.clip(-3, 3) / 3.0
        return result.fillna(0)
