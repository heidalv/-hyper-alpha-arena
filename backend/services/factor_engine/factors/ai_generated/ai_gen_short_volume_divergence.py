"""AI因子: 做空量价背离 | 置信:58% | 捕捉做空亏损模式中常见的量价背离：价格小幅下跌但成交量异常放大，表明空头拥挤且抛压可能衰竭，容易引发反弹止损。因子在价格下跌但成交量激增时给出负值（做空风险高），在价格下跌且成交量萎缩时给出正值（做空安全）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortVolumeDivergence(BaseFactor):
    """捕捉做空亏损模式中常见的量价背离：价格小幅下跌但成交量异常放大，表明空头拥挤且抛压可能衰竭，容易引发反弹止损。因子在价格下跌但成交量激增时给出负值（做空风险高），在价格下跌且成交量萎缩时给出正值（做空安全）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_volume_divergence",
            name="Short Volume Divergence",
            display_name="做空量价背离",
            description="捕捉做空亏损模式中常见的量价背离：价格小幅下跌但成交量异常放大，表明空头拥挤且抛压可能衰竭，容易引发反弹止损。因子在价格下跌但成交量激增时给出负值（做空风险高），在价格下跌且成交量萎缩时给出正值（做空安全）。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(3)
        vol_ma = data['volume'].rolling(10).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-9)
        down_vol = (ret < 0) * vol_ratio
        up_vol = (ret > 0) * vol_ratio
        result = -down_vol + up_vol
        result = result / (result.abs().rolling(20).max() + 1e-9)
        result = result.clip(-1, 1)
        return result.fillna(0)
