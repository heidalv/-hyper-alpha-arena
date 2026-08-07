"""AI因子: 微利平仓风险因子 | 置信:60% | 衡量价格在近期窄幅区间内震荡的程度，窄幅震荡时间越长，越容易触发微利平仓导致亏损。通过计算过去N根K线中价格区间宽度相对于平均真实波动的比例，并结合价格在区间内停留的时间。输出[-1,+1]，正值表示高风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MicroProfitRisk(BaseFactor):
    """衡量价格在近期窄幅区间内震荡的程度，窄幅震荡时间越长，越容易触发微利平仓导致亏损。通过计算过去N根K线中价格区间宽度相对于平均真实波动的比例，并结合价格在区间内停留的时间。输出[-1,+1]，正值表示高风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_micro_profit_risk",
            name="MicroProfitRisk",
            display_name="微利平仓风险因子",
            description="衡量价格在近期窄幅区间内震荡的程度，窄幅震荡时间越长，越容易触发微利平仓导致亏损。通过计算过去N根K线中价格区间宽度相对于平均真实波动的比例，并结合价格在区间内停留的时间。输出[-1,+1]，正值表示高风险。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        window = 20
        # 计算真实波幅ATR
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(window).mean()
        # 近期最高最低
        recent_high = high.rolling(window).max()
        recent_low = low.rolling(window).min()
        range_width = recent_high - recent_low
        # 窄幅阈值：宽度小于0.5倍ATR
        narrow = (range_width < 0.5 * atr).astype(float)
        # 价格在区间内的相对位置
        mid = (recent_high + recent_low) / 2
        dev = (close - mid) / (range_width + 1e-10)
        # 综合得分：窄幅时dev接近0表示在中间，风险高；极端位置风险低
        score = narrow * (1 - abs(dev)) * 2 - 1
        return score.fillna(0).clip(-1, 1)
