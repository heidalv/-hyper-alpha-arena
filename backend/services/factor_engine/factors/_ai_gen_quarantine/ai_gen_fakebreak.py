"""AI因子: 假突破检测因子 | 置信:60% | 检测价格突破近期高点后是否迅速回落。计算过去n天最高价，若当日最高价突破该高点但收盘价低于突破点一定比例，则认为是假突破，输出负值；若突破后收盘价站稳则输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Fake Breakout Detector(BaseFactor):
    """检测价格突破近期高点后是否迅速回落。计算过去n天最高价，若当日最高价突破该高点但收盘价低于突破点一定比例，则认为是假突破，输出负值；若突破后收盘价站稳则输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fakebreak",
            name="Fake Breakout Detector",
            display_name="假突破检测因子",
            description="检测价格突破近期高点后是否迅速回落。计算过去n天最高价，若当日最高价突破该高点但收盘价低于突破点一定比例，则认为是假突破，输出负值；若突破后收盘价站稳则输出正值。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            lookback = 20
            # 前n日最高价
            high_prev = data['high'].rolling(lookback, min_periods=1).max().shift(1)
            # 当日最低价
            low_current = data['low']
            # 突破条件：当日最高价 > 前n日最高价 且 收盘价 < 突破价 - 0.2*ATR
            atr = (data['high'] - data['low']).rolling(14, min_periods=1).mean()
            breakout = (data['high'] > high_prev) & (data['close'] < high_prev - 0.2 * atr)
            # 成功突破：突破后收盘价高于突破价
            strong_break = (data['high'] > high_prev) & (data['close'] >= high_prev)
            # 生成信号：假突破-1，成功突破+1，否则0
            result = pd.Series(0.0, index=data.index)
            result[breakout] = -1.0
            result[strong_break] = 1.0
            # 平滑处理避免突变
            result = result.rolling(3, min_periods=1).mean()
            return result
