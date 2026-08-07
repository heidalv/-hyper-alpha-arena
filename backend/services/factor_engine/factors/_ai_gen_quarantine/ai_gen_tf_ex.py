"""AI因子: 趋势衰竭因子 | 置信:60% | 价格远离短期均线，同时成交量萎缩，预示上涨动能衰竭，容易导致多头持仓超时或反转亏损。因子值接近+1表示衰竭风险极高（不利多头），接近-1表示趋势健康（有利多头）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendFatigueExhaustion(BaseFactor):
    """价格远离短期均线，同时成交量萎缩，预示上涨动能衰竭，容易导致多头持仓超时或反转亏损。因子值接近+1表示衰竭风险极高（不利多头），接近-1表示趋势健康（有利多头）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tf_ex",
            name="Trend Fatigue Exhaustion",
            display_name="趋势衰竭因子",
            description="价格远离短期均线，同时成交量萎缩，预示上涨动能衰竭，容易导致多头持仓超时或反转亏损。因子值接近+1表示衰竭风险极高（不利多头），接近-1表示趋势健康（有利多头）。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        # 短期EMA
        ema_fast = close.ewm(span=12, adjust=False).mean()
        # 价格偏离度
        deviation = (close - ema_fast) / ema_fast
        # 成交量比值：当前成交量 / 过去N期平均成交量
        vol_ratio = volume / volume.rolling(20).mean()
        # 偏离度标准化（用滚动Z-score）
        dev_z = (deviation - deviation.rolling(60).mean()) / deviation.rolling(60).std()
        # 成交量比值Z-score
        vol_z = (vol_ratio - vol_ratio.rolling(60).mean()) / vol_ratio.rolling(60).std()
        # 合成：高偏离+低成交量=衰竭风险高，分数高
        raw = dev_z - vol_z  # 偏离高为正，成交量低时vol_z为负，所以减负得正，增大
        # 归一化到[-1,1]用tanh
        result = np.tanh(raw / 2.0)
        return result
