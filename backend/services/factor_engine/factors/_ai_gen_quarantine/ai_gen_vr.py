"""AI因子: 波动率状态 | 置信:60% | 基于ATR与过去N天ATR均值的比值判断当前波动率是否异常。当波动率过低（比值<0.8）时市场可能进入平静期但易突发波动导致止损，因子值偏向-1；当波动率过高（比值>1.5）时易被扫损，因子值也偏向-1；中等波动率（0.9-1.2）视为正常，因子值偏向+1。以此过滤极端波动率场景。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatilityregime(BaseFactor):
    """基于ATR与过去N天ATR均值的比值判断当前波动率是否异常。当波动率过低（比值<0.8）时市场可能进入平静期但易突发波动导致止损，因子值偏向-1；当波动率过高（比值>1.5）时易被扫损，因子值也偏向-1；中等波动率（0.9-1.2）视为正常，因子值偏向+1。以此过滤极端波动率场景。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vr",
            name="VolatilityRegime",
            display_name="波动率状态",
            description="基于ATR与过去N天ATR均值的比值判断当前波动率是否异常。当波动率过低（比值<0.8）时市场可能进入平静期但易突发波动导致止损，因子值偏向-1；当波动率过高（比值>1.5）时易被扫损，因子值也偏向-1；中等波动率（0.9-1.2）视为正常，因子值偏向+1。以此过滤极端波动率场景。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR（14日）
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = np.maximum(high - low, np.abs(high - prev_close), np.abs(low - prev_close))
        atr = tr.rolling(window=14).mean()
        # 计算过去60日ATR均值作为基准
        atr_baseline = atr.rolling(window=60, min_periods=14).mean()
        # 比值
        ratio = atr / atr_baseline
        # 映射到[-1,1]: 比值在0.8以下 -> -1；0.8-0.9线性上升；0.9-1.2 -> +1；1.2-1.5线性下降；1.5以上 -> -1
        def map_ratio(x):
            if x < 0.8:
                return -1.0
            elif x < 0.9:
                return (x - 0.8) / 0.1 * 2 - 1  # 从-1到+1
            elif x <= 1.2:
                return 1.0
            elif x <= 1.5:
                return 1.0 - (x - 1.2) / 0.3 * 2  # 从+1到-1
            else:
                return -1.0
        result = ratio.apply(map_ratio)
        result = result.fillna(0)
        return result
