"""AI因子: 价格一致性指数 | 置信:60% | 基于日内价格路径的‘弯曲程度’衡量价格运动的有序性。使用HL/CO比率和成交量确认，当价格走势光滑（低弯曲）时认为状态明确；当价格来回震荡（高弯曲）时认为未知状态。计算：近N根K线的平均HL/OC比率，并比较其与成交量变化的关系，输出[-1,1]区间，正值表示有序上涨，负值表示有序下跌，0表示混乱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceCoherenceIndex(BaseFactor):
    """基于日内价格路径的‘弯曲程度’衡量价格运动的有序性。使用HL/CO比率和成交量确认，当价格走势光滑（低弯曲）时认为状态明确；当价格来回震荡（高弯曲）时认为未知状态。计算：近N根K线的平均HL/OC比率，并比较其与成交量变化的关系，输出[-1,1]区间，正值表示有序上涨，负值表示有序下跌，0表示混乱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_coherence",
            name="Price Coherence Index",
            display_name="价格一致性指数",
            description="基于日内价格路径的‘弯曲程度’衡量价格运动的有序性。使用HL/CO比率和成交量确认，当价格走势光滑（低弯曲）时认为状态明确；当价格来回震荡（高弯曲）时认为未知状态。计算：近N根K线的平均HL/OC比率，并比较其与成交量变化的关系，输出[-1,1]区间，正值表示有序上涨，负值表示有序下跌，0表示混乱。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        open_ = data['open']
        close = data['close']
        volume = data['volume']
        # 日内振幅与实体的比率
        hlc = high - low
        oc = np.abs(close - open_)
        ratio = hlc / (oc + 1e-10)
        # 高比率表示杂乱，低比率表示有序
        smoothness = 1.0 / (ratio + 1.0)  # 0~1
        # 结合成交量变化：如果成交量放大且有序，方向更可信
        vol_ma5 = volume.rolling(5).mean()
        vol_ratio = volume / (vol_ma5 + 1e-10)
        # 方向：收盘相对于开盘
        dir_raw = (close - open_) / (high - low + 1e-10)  # 大致方向
        # 综合：有序的正向运动为正，负向为负，混乱则靠近0
        coherence = smoothness * dir_raw
        # 成交量加权
        vol_weight = np.clip(vol_ratio, 0.5, 2.0) - 0.5  # 0~1.5
        result = coherence * vol_weight
        return result.fillna(0).clip(-1,1)
