"""AI因子: 假突破反转 | 置信:60% | 检测价格突破近期极值但成交量不足，预示假突破后的反转。当收盘价突破过去10根K线最高价且成交量低于同期均值时，发出做空信号（-1）；反之突破最低价且缩量时做多（+1）。信号强度通过突破幅度与成交量的比值做tanh归一化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class FakeBreakoutReversal(BaseFactor):
    """检测价格突破近期极值但成交量不足，预示假突破后的反转。当收盘价突破过去10根K线最高价且成交量低于同期均值时，发出做空信号（-1）；反之突破最低价且缩量时做多（+1）。信号强度通过突破幅度与成交量的比值做tanh归一化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fake_breakout",
            name="Fake Breakout Reversal",
            display_name="假突破反转",
            description="检测价格突破近期极值但成交量不足，预示假突破后的反转。当收盘价突破过去10根K线最高价且成交量低于同期均值时，发出做空信号（-1）；反之突破最低价且缩量时做多（+1）。信号强度通过突破幅度与成交量的比值做tanh归一化。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        lookback = 10
        # 计算近期最高价和最低价
        high = data['high'].rolling(lookback).max().shift(1)
        low = data['low'].rolling(lookback).min().shift(1)
        # 计算近期平均成交量
        avg_vol = data['volume'].rolling(lookback).mean().shift(1)
        # 当前收盘价和成交量
        close = data['close']
        vol = data['volume']
        # 突破条件：价格突破且成交量低于均值
        long_signal = (close < low) & (vol < avg_vol)
        short_signal = (close > high) & (vol < avg_vol)
        # 计算突破幅度
        long_mag = (low - close) / close
        short_mag = (close - high) / close
        # 组合信号，使用tanh限制在[-1,1]
        raw = np.where(short_signal, -np.tanh(short_mag * 10), 0)
        raw = np.where(long_signal, np.tanh(long_mag * 10), raw)
        return pd.Series(raw, index=data.index)
