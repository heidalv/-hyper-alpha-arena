"""AI因子: 成交量确认趋势强度 | 置信:50% | 亏损集中在regime=unknown，且包含master_running_close，表明在趋势不明时成交量无法确认方向。该因子结合价格动量与成交量变化，当价格上涨但成交量萎缩时做空（+1），下跌但成交量放大时做多（-1），过滤无效趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volumeconfirmationtrend(BaseFactor):
    """亏损集中在regime=unknown，且包含master_running_close，表明在趋势不明时成交量无法确认方向。该因子结合价格动量与成交量变化，当价格上涨但成交量萎缩时做空（+1），下跌但成交量放大时做多（-1），过滤无效趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_confirm",
            name="VolumeConfirmationTrend",
            display_name="成交量确认趋势强度",
            description="亏损集中在regime=unknown，且包含master_running_close，表明在趋势不明时成交量无法确认方向。该因子结合价格动量与成交量变化，当价格上涨但成交量萎缩时做空（+1），下跌但成交量放大时做多（-1），过滤无效趋势。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(10)
        vol_ma5 = data['volume'].rolling(5).mean()
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_ratio = vol_ma5 / (vol_ma20 + 1e-9)
        price_up_vol_down = ((ret > 0).astype(float) * (vol_ratio < 1).astype(float))
        price_down_vol_up = ((ret < 0).astype(float) * (vol_ratio > 1).astype(float))
        result = (price_up_vol_down - price_down_vol_up).clip(-1, 1)
        return result
