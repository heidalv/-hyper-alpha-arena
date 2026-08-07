"""AI因子: 成交量异常 | 置信:65% | 检测成交量相对于其移动平均的异常偏离，结合价格方向。当成交量显著萎缩且价格波动较大时，可能预示假突破或流动性不足，为高风险状态。输出-1到0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeAbnormality(BaseFactor):
    """检测成交量相对于其移动平均的异常偏离，结合价格方向。当成交量显著萎缩且价格波动较大时，可能预示假突破或流动性不足，为高风险状态。输出-1到0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volab",
            name="VolumeAbnormality",
            display_name="成交量异常",
            description="检测成交量相对于其移动平均的异常偏离，结合价格方向。当成交量显著萎缩且价格波动较大时，可能预示假突破或流动性不足，为高风险状态。输出-1到0。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
        price_ret = close.pct_change().abs()
        # 低成交量且价格波动大 => 风险高
        risk = -1 * ((vol_ratio < 0.5) & (price_ret > price_ret.rolling(20).mean())).astype(float)
        risk = risk.fillna(0)
        return risk
