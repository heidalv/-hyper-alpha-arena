"""AI因子: 未知状态识别 | 置信:60% | 基于波动率收缩与价格在近期区间的位置，识别市场处于低信噪比震荡状态。当ATR比率低于0.8且价格位于区间中部（20%-80%分位）时，判断为未知状态，输出负信号；否则输出正信号。使用tanh将原始得分映射到[-1,1]连续区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknown_Regime_Detector(BaseFactor):
    """基于波动率收缩与价格在近期区间的位置，识别市场处于低信噪比震荡状态。当ATR比率低于0.8且价格位于区间中部（20%-80%分位）时，判断为未知状态，输出负信号；否则输出正信号。使用tanh将原始得分映射到[-1,1]连续区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unkn",
            name="Unknown Regime Detector",
            display_name="未知状态识别",
            description="基于波动率收缩与价格在近期区间的位置，识别市场处于低信噪比震荡状态。当ATR比率低于0.8且价格位于区间中部（20%-80%分位）时，判断为未知状态，输出负信号；否则输出正信号。使用tanh将原始得分映射到[-1,1]连续区间。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # 计算20日ATR
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        atr_ma = atr.rolling(20).mean()
        ratio = atr / atr_ma
        # 计算20日价格位置
        low_20 = low.rolling(20).min()
        high_20 = high.rolling(20).max()
        pos = (close - low_20) / (high_20 - low_20)
        # 原始得分：当ratio低且pos靠近中间时负向，其他正向
        raw = (ratio - 0.8) * (pos - 0.5) * 10
        result = pd.Series(np.tanh(raw), index=data.index)
        return result
