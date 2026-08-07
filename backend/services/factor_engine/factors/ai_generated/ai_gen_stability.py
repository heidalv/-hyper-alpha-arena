"""AI因子: 趋势稳定性因子 | 置信:60% | 用最近5根K线的ATR与价格变化绝对值的比值衡量趋势是否稳定，同时结合连续同向K线个数。比值高（波动大、回调多）视为不稳定，给予负信号；比值低且连续同向视为稳定，给予正信号。适用于规避止损高发的震荡或假突破行情。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStabilityIndex(BaseFactor):
    """用最近5根K线的ATR与价格变化绝对值的比值衡量趋势是否稳定，同时结合连续同向K线个数。比值高（波动大、回调多）视为不稳定，给予负信号；比值低且连续同向视为稳定，给予正信号。适用于规避止损高发的震荡或假突破行情。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stability",
            name="Trend Stability Index",
            display_name="趋势稳定性因子",
            description="用最近5根K线的ATR与价格变化绝对值的比值衡量趋势是否稳定，同时结合连续同向K线个数。比值高（波动大、回调多）视为不稳定，给予负信号；比值低且连续同向视为稳定，给予正信号。适用于规避止损高发的震荡或假突破行情。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # ATR
        df['tr'] = np.maximum(df['high'] - df['low'], 
                              np.maximum(abs(df['high'] - df['close'].shift(1)), 
                                         abs(df['low'] - df['close'].shift(1))))
        df['atr'] = df['tr'].rolling(5).mean()
        # 价格变化绝对值
        df['close_chg'] = abs(df['close'].diff())
        # 稳定性比率：ATR与价格变化之比，越大表示波动中回调小？实际上我们希望价格变化相对ATR小则稳定
        # 用价格变化除以ATR，越小越稳定
        df['stability_ratio'] = df['close_chg'] / (df['atr'] + 1e-10)
        # 连续同向K线个数（看涨/看跌）
        df['direction'] = np.sign(df['close'].diff())
        df['consec'] = (df['direction'] == df['direction'].shift(1)).cumsum()
        # 重置计数
        df['consec'] = df.groupby((df['direction'] != df['direction'].shift(1)).cumsum()).cumcount() + 1
        # 信号：稳定性比率低且连续K线>2视为趋势稳定，信号+1；反之-1
        cond_stable = (df['stability_ratio'] < 0.5) & (df['consec'] >= 3)
        cond_unstable = (df['stability_ratio'] >= 1.0) | (df['consec'] == 1)
        signal = pd.Series(0.0, index=df.index)
        signal[cond_stable] = 1.0
        signal[cond_unstable] = -1.0
        return signal
