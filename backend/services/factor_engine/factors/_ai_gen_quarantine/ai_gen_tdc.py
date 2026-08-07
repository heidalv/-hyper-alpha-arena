"""AI因子: 时间衰减修正 | 置信:65% | 模拟持有时间过长导致的衰减风险，通过计算连续上涨/下跌的持续时间与波动率调整。当价格连续窄幅震荡或缓慢移动时，因子向负值偏移，提示持仓超时风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Time_Decay_Correction(BaseFactor):
    """模拟持有时间过长导致的衰减风险，通过计算连续上涨/下跌的持续时间与波动率调整。当价格连续窄幅震荡或缓慢移动时，因子向负值偏移，提示持仓超时风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tdc",
            name="Time_Decay_Correction",
            display_name="时间衰减修正",
            description="模拟持有时间过长导致的衰减风险，通过计算连续上涨/下跌的持续时间与波动率调整。当价格连续窄幅震荡或缓慢移动时，因子向负值偏移，提示持仓超时风险。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算价格相对于前一日的方向: 1涨 -1跌 0平
        direction = np.sign(close.diff())
        # 连续同向计数
        streak = np.zeros_like(close)
        for i in range(1, len(close)):
            if direction.iloc[i] == direction.iloc[i-1] and direction.iloc[i] != 0:
                streak[i] = streak[i-1] + 1
            else:
                streak[i] = 0
        # 波动率调整: 近期ATR
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(10).mean() / close
        # 当连续时间过长且波动率低时，风险高
        risk = streak * atr * 5
        # 归一化到[-1,1]
        result = -np.tanh(risk)
        return result.fillna(0).clip(-1,1)
