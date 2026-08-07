"""AI因子: 缩量盘整因子 | 置信:55% | 识别成交量萎缩的盘整状态。比较当前成交量与过去N日成交量的均值，当成交量显著低于均值且价格波动较小时，市场容易进入无聊震荡。使用成交量比率和价格范围乘积的负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Slump_Indicator(BaseFactor):
    """识别成交量萎缩的盘整状态。比较当前成交量与过去N日成交量的均值，当成交量显著低于均值且价格波动较小时，市场容易进入无聊震荡。使用成交量比率和价格范围乘积的负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volslump",
            name="Volume Slump Indicator",
            display_name="缩量盘整因子",
            description="识别成交量萎缩的盘整状态。比较当前成交量与过去N日成交量的均值，当成交量显著低于均值且价格波动较小时，市场容易进入无聊震荡。使用成交量比率和价格范围乘积的负值。",
            category="volume",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        volume = data['volume']
        high = data['high']
        low = data['low']
        n = 20
        vol_ma = volume.rolling(n).mean()
        vol_ratio = volume / vol_ma
        price_range = (high - low) / (high.rolling(n).mean() - low.rolling(n).mean() + 1e-10)
        # 缩量且价格范围缩小 => 因子值高（正值表示盘整风险上升，信号做空或平仓长期持仓？这里设计为正值指示不宜追趋势）
        raw = (1 - vol_ratio) * (1 - price_range)
        result = raw.clip(-1, 1)
        return result
