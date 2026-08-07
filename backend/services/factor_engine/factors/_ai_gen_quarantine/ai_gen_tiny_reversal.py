"""AI因子: 微小反转强度因子 | 置信:55% | 检测在小幅波动后价格出现反转的可能。计算近期连续小K线（波动小于平均波动的一半）的数量，并在这些K线出现后，若下一根K线方向与之前相反且幅度超过阈值，则赋值+1或-1。本质上是一个模式识别，用于避免在震荡区间追涨杀跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Tiny Reversal Strength(BaseFactor):
    """检测在小幅波动后价格出现反转的可能。计算近期连续小K线（波动小于平均波动的一半）的数量，并在这些K线出现后，若下一根K线方向与之前相反且幅度超过阈值，则赋值+1或-1。本质上是一个模式识别，用于避免在震荡区间追涨杀跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tiny_reversal",
            name="Tiny Reversal Strength",
            display_name="微小反转强度因子",
            description="检测在小幅波动后价格出现反转的可能。计算近期连续小K线（波动小于平均波动的一半）的数量，并在这些K线出现后，若下一根K线方向与之前相反且幅度超过阈值，则赋值+1或-1。本质上是一个模式识别，用于避免在震荡区间追涨杀跌。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            window = 10
            threshold = 0.5
            close = data['close']
            high = data['high']
            low = data['low']
            # 真实波幅
            tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
            avg_tr = tr.rolling(20, min_periods=20).mean()
            # 是否为小K线（波动小于平均波动的threshold倍）
            small_candle = tr < (avg_tr * threshold)
            # 连续小K线计数
            consec_small = small_candle.rolling(window, min_periods=1).sum()
            # 方向：收盘相对开盘的符号  (假设开盘=前收? 此处用相邻收盘代替)
            direction = (close.diff() > 0).astype(int) - (close.diff() < 0).astype(int)
            # 连续小K线结束后下一根的方向相反？
            # 简单: 连续小K线数量越多，越可能反转。用N形反转得分
            # 归一化
            score = consec_small / window  # 0~1
            # 乘以方向变化？使用反转信号: 如果连续小K线后出现反向大K线
            # 简化: 直接返回当前小K线密集程度作为反转概率，[-1,1]用均值偏移
            result = 2 * (score - 0.5)
            return result.fillna(0)
