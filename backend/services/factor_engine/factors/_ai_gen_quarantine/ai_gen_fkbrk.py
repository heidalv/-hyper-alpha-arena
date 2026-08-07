"""AI因子: 假突破反转因子 | 置信:60% | 识别价格突破近期高点/低点后迅速回落的假突破模式。当收盘价突破过去5日最高点，但收盘价低于日内高点超过0.5%，且成交量超过20日均量的1.5倍时，发出做空信号(-1)；类似地，向下假突破做多(+1)。连续计算信号强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class FalseBreakoutReversal(BaseFactor):
    """识别价格突破近期高点/低点后迅速回落的假突破模式。当收盘价突破过去5日最高点，但收盘价低于日内高点超过0.5%，且成交量超过20日均量的1.5倍时，发出做空信号(-1)；类似地，向下假突破做多(+1)。连续计算信号强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fkbrk",
            name="False Breakout Reversal",
            display_name="假突破反转因子",
            description="识别价格突破近期高点/低点后迅速回落的假突破模式。当收盘价突破过去5日最高点，但收盘价低于日内高点超过0.5%，且成交量超过20日均量的1.5倍时，发出做空信号(-1)；类似地，向下假突破做多(+1)。连续计算信号强度。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        # 参数
        lookback = 5
        vol_factor = 1.5
        retrace_threshold = 0.005  # 0.5%
        # 计算过去5日最高最低
        roll_high = df['high'].rolling(lookback).max().shift(1)
        roll_low = df['low'].rolling(lookback).min().shift(1)
        # 成交量均值
        vol_ma20 = df['volume'].rolling(20).mean()
        # 假向上突破：收盘价 > 前5日最高，且收盘价 < 当日最高 - 阈值，且成交量放大
        up_break = (df['close'] > roll_high) & (df['close'] < df['high'] - retrace_threshold * df['high'])
        up_vol = df['volume'] > vol_factor * vol_ma20
        short_signal = np.where(up_break & up_vol, -1.0, 0.0)
        # 假向下突破：收盘价 < 前5日最低，且收盘价 > 当日最低 + 阈值
        down_break = (df['close'] < roll_low) & (df['close'] > df['low'] + retrace_threshold * df['low'])
        down_vol = df['volume'] > vol_factor * vol_ma20
        long_signal = np.where(down_break & down_vol, 1.0, 0.0)
        # 合并信号，确保不冲突（理论上不会同时出现）
        result = pd.Series(long_signal + short_signal, index=df.index).fillna(0.0)
        return result
