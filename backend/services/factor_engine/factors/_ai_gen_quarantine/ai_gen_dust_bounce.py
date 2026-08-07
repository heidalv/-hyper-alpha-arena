"""AI因子: 尘埃清理反弹 | 置信:60% | 识别价格在小幅震荡后突然被拉向一个方向但随即反转，类似市场清除止损单（dust_cleanup）。利用价格极窄幅波动后突然放量突破前低/前高并快速回到区间内，产生反向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupBounce(BaseFactor):
    """识别价格在小幅震荡后突然被拉向一个方向但随即反转，类似市场清除止损单（dust_cleanup）。利用价格极窄幅波动后突然放量突破前低/前高并快速回到区间内，产生反向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dust_bounce",
            name="Dust Cleanup Bounce",
            display_name="尘埃清理反弹",
            description="识别价格在小幅震荡后突然被拉向一个方向但随即反转，类似市场清除止损单（dust_cleanup）。利用价格极窄幅波动后突然放量突破前低/前高并快速回到区间内，产生反向信号。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 计算过去10周期的真实波幅（TR）的移动平均
        df['tr'] = pd.concat([df['high'] - df['low'], abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))], axis=1).max(axis=1)
        df['atr10'] = df['tr'].rolling(10).mean()
        # 识别窄幅波动：过去5日ATR相对于过去20日ATR的比例较低
        df['atr5'] = df['tr'].rolling(5).mean()
        df['atr_ratio'] = df['atr5'] / df['atr10']
        # 窄幅定义为atr_ratio < 0.5
        cond_narrow = df['atr_ratio'] < 0.5
        # 计算价格区间：过去10日最高价和最低价
        df['hh10'] = df['high'].rolling(10).max()
        df['ll10'] = df['low'].rolling(10).min()
        # 突破条件：收盘价突破前10日高点或低点，且成交量放大
        df['vol_ma10'] = df['volume'].rolling(10).mean()
        cond_break_up = (df['close'] > df['hh10'].shift(1)) & (df['volume'] > df['vol_ma10'] * 1.5)
        cond_break_down = (df['close'] < df['ll10'].shift(1)) & (df['volume'] > df['vol_ma10'] * 1.5)
        # 反转条件：突破后下一根K线立即回到区间内
        cond_rev_down = cond_break_up.shift(1) & (df['close'] < df['hh10'].shift(2))  # 回到高点之下
        cond_rev_up = cond_break_down.shift(1) & (df['close'] > df['ll10'].shift(2))   # 回到低点之上
        # 同时要求窄幅背景
        signal = pd.Series(0, index=df.index)
        signal[cond_rev_down & cond_narrow.shift(1)] = -1.0
        signal[cond_rev_up & cond_narrow.shift(1)] = 1.0
        return signal
