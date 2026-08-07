"""AI因子: 震荡反转风险因子 | 置信:60% | 利用布林带宽度收缩识别震荡区间，当价格处于区间上轨附近时，给出做空风险警示（负值），捕捉假突破后的多头亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RangeBoundReversalRisk(BaseFactor):
    """利用布林带宽度收缩识别震荡区间，当价格处于区间上轨附近时，给出做空风险警示（负值），捕捉假突破后的多头亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rb",
            name="Range-Bound Reversal Risk",
            display_name="震荡反转风险因子",
            description="利用布林带宽度收缩识别震荡区间，当价格处于区间上轨附近时，给出做空风险警示（负值），捕捉假突破后的多头亏损。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 20-period Bollinger Bands
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        # 带宽归一化 (近期最小值相对当前带宽)
        bandwidth = (upper - lower) / ma
        bw_min = bandwidth.rolling(100).min()
        bw_ratio = bandwidth / bw_min.replace(0, np.nan)  # >1, 1附近表示极度收缩
        bw_score = np.tanh((1.0 / bw_ratio - 1) * 5)  # 收缩时接近1，扩张时接近-1
        # 价格位置 0~1 在上轨为1，下轨为0
        price_position = (close - lower) / (upper - lower).replace(0, np.nan)
        pos_scaled = (price_position - 0.5) * 2  # -1 下轨, +1 上轨
        # 组合：收缩区间 + 上轨 = 强烈看空 (负值)
        raw = -pos_scaled * bw_score
        result = raw.clip(-1, 1)
        return result
