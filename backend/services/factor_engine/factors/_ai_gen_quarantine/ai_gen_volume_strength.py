"""AI因子: 成交量背离指标 | 置信:50% | 结合成交量变化与价格方向，识别量价背离。当成交量异常放大而价格无明显趋势时，市场状态模糊，输出负值；量价配合良好时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeDivergenceIndicator(BaseFactor):
    """结合成交量变化与价格方向，识别量价背离。当成交量异常放大而价格无明显趋势时，市场状态模糊，输出负值；量价配合良好时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_strength",
            name="Volume Divergence Indicator",
            display_name="成交量背离指标",
            description="结合成交量变化与价格方向，识别量价背离。当成交量异常放大而价格无明显趋势时，市场状态模糊，输出负值；量价配合良好时输出正值。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格变化方向
        price_change = close.pct_change()
        # 成交量Z-score
        vol_ma = volume.rolling(20).mean()
        vol_std = volume.rolling(20).std()
        vol_z = (volume - vol_ma) / (vol_std + 1e-10)
        # 价格变化绝对值
        abs_price_change = price_change.abs()
        # 当成交量异常大而价格变化很小时，认为背离
        # 使用组合信号：大成交量但价格变化小 => 负值
        signal = -np.tanh(vol_z * (0.5 - abs_price_change * 10))
        return signal.fillna(0)
