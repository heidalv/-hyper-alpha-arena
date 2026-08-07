"""AI因子: 量价同步因子 | 置信:55% | 衡量成交量变化与价格变化的同步性，当放量上涨或放量下跌时输出正值（趋势信号），当缩量震荡或量价背离时输出负值（噪音信号）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Sync(BaseFactor):
    """衡量成交量变化与价格变化的同步性，当放量上涨或放量下跌时输出正值（趋势信号），当缩量震荡或量价背离时输出负值（噪音信号）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_sync",
            name="Volume Price Sync",
            display_name="量价同步因子",
            description="衡量成交量变化与价格变化的同步性，当放量上涨或放量下跌时输出正值（趋势信号），当缩量震荡或量价背离时输出负值（噪音信号）。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        period = 14
        # 价格变化
        ret = close.pct_change()
        # 成交量变化
        vol_change = volume.pct_change()
        # 滚动相关系数
        corr = ret.rolling(period).corr(vol_change)
        # 同时考虑方向: 正相关且同向放量=趋势，负相关=背离
        # 将相关系数映射到[-1,1]并乘以方向强度
        direction = np.sign(ret.fillna(0))
        sync = corr * direction
        sync = sync.clip(-1, 1)
        return sync.fillna(0)
