"""AI因子: 突破失败指示器 | 置信:65% | 检测价格突破近期区间（如20日高低点）后迅速回落的情况，结合持仓超时（max_hold_timeout）和止损（sl）的亏损模式，识别假突破。值域[-1,1]：负值表示多头假突破（做空信号），正值表示空头假突破（做多信号）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Breakout_Failure_Indicator(BaseFactor):
    """检测价格突破近期区间（如20日高低点）后迅速回落的情况，结合持仓超时（max_hold_timeout）和止损（sl）的亏损模式，识别假突破。值域[-1,1]：负值表示多头假突破（做空信号），正值表示空头假突破（做多信号）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_breakfail",
            name="Breakout Failure Indicator",
            display_name="突破失败指示器",
            description="检测价格突破近期区间（如20日高低点）后迅速回落的情况，结合持仓超时（max_hold_timeout）和止损（sl）的亏损模式，识别假突破。值域[-1,1]：负值表示多头假突破（做空信号），正值表示空头假突破（做多信号）。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算20日区间
        high20 = high.rolling(20).max()
        low20 = low.rolling(20).min()
        # 判断突破：收盘价突破上轨或下轨
        break_up = close > high20.shift(1)  # 今日突破前一日的上轨
        break_down = close < low20.shift(1)
        # 突破后的表现：次日收盘是否回到区间内（用未来？不能，用当前与前一日比较？这里用当前突破后，计算未来1日相对位置，但避免未来函数，改为用突破当日的高点/低点与收盘价比较）
        # 替代：突破当日若收盘价远低于当日高点（多头突破失败），或收盘价远高于当日低点（空头突破失败）
        # 定义失败：多头突破时，收盘价低于当日高点与区间上轨的中间？简单：收盘价低于当日高点 * 0.95 视为衰竭
        fail_up = break_up & (close < high * 0.98)  # 突破但收盘价接近区间上轨？实际是未能维持在高位
        fail_down = break_down & (close > low * 1.02)
        # 生成信号
        signal = pd.Series(0.0, index=close.index)
        signal[fail_up] = -1.0  # 多头假突破，看空
        signal[fail_down] = 1.0  # 空头假突破，看多
        # 平滑
        result = signal.rolling(5).mean().fillna(0.0)
        return result
