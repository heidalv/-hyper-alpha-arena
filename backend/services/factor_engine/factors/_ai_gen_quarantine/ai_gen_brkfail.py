"""AI因子: 突破失败检测因子 | 置信:65% | 检测价格突破近期高点或低点后迅速回撤的现象，成交量在突破时未能有效放大，随后价格返回区间内，此类模式常见于假突破导致止损亏损。因子值负向表示突破失败概率高"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Breakout_Failure_Indicator(BaseFactor):
    """检测价格突破近期高点或低点后迅速回撤的现象，成交量在突破时未能有效放大，随后价格返回区间内，此类模式常见于假突破导致止损亏损。因子值负向表示突破失败概率高"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_brkfail",
            name="Breakout_Failure_Indicator",
            display_name="突破失败检测因子",
            description="检测价格突破近期高点或低点后迅速回撤的现象，成交量在突破时未能有效放大，随后价格返回区间内，此类模式常见于假突破导致止损亏损。因子值负向表示突破失败概率高",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        lookback = 10
        # 前N日最高价、最低价
        prev_high = high.rolling(lookback).max().shift(1)
        prev_low = low.rolling(lookback).min().shift(1)
        # 当前价格突破前高/前低
        breakout_up = close > prev_high * 1.005  # 微小突破
        breakout_down = close < prev_low * 0.995
        # 成交量验证：突破时成交量是否大于前N日均量
        vol_ma = volume.rolling(lookback).mean().shift(1)
        vol_confirm = volume > vol_ma * 1.2
        # 回撤检测：突破后一根K线收盘回到区间内
        # 由于是future信息，这里用当前K线后的close? 注意避免未来函数。可以用shift(-1)但需注意。此处使用close.shift(-1)作为下一根K线
        close_next = close.shift(-1)
        # 向上突破失败：next close < prev_high
        fail_up = breakout_up & (close_next < prev_high)
        # 向下突破失败：next close > prev_low
        fail_down = breakout_down & (close_next > prev_low)
        # 综合得分：有突破但成交量未确认且随后回撤
        # 定义失败信号为1，否则0
        fail_signal = ((fail_up | fail_down) & ~vol_confirm).astype(int)
        # 平滑处理：滚动求和后归一化到[-1,1]，负数表示失败风险高
        roll_sum = fail_signal.rolling(5).sum() / 5.0
        result = -roll_sum  # 0->0, 1->-1
        # 填充NaN
        result = result.fillna(0)
        return result
