"""AI因子: 趋势置信度 | 置信:60% | 基于ADX和价格相对位置计算趋势置信度，ADX越高且价格在近期区间极端位置时，因子绝对值接近1，方向与趋势一致；ADX低时因子接近0，表示市场状态不明。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend_Confidence(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_tcon", name="Trend_Confidence",
        display_name="趋势置信度", description="基于ADX和价格相对位置计算趋势置信度，ADX越高且价格在近期区间极端位置时，因子绝对值接近1，方向与趋势一致；ADX低时因子接近0，表示市场状态不明。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    high, low, close = data['high'].values, data['low'].values, data['close'].values
    # 计算ATR和ADX
    period = 14
    tr = np.maximum(high[1:] - low[1:], np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1]))
    atr = np.concatenate([[np.nan], np.convolve(tr, np.ones(period)/period, mode='valid')])
    # 计算+DM和-DM
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    # 平滑
    plus_di = 100 * np.convolve(plus_dm, np.ones(period)/period, mode='valid') / atr[period:]
    minus_di = 100 * np.convolve(minus_dm, np.ones(period)/period, mode='valid') / atr[period:]
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = np.concatenate([[np.nan]* (2*period-1), np.convolve(dx, np.ones(period)/period, mode='valid')])
    # 价格位置
    roll_high = np.maximum.accumulate(high[-200:]) if len(high)>=200 else np.max(high)
    roll_low = np.minimum.accumulate(low[-200:]) if len(low)>=200 else np.min(low)
    pos = (close - np.min(high[-200:])) / (np.max(high[-200:]) - np.min(high[-200:]) + 1e-10)
    pos = 2*pos - 1  # [-1,1]
    # 综合：ADX标准化到[0,1]，乘以方向
    adx_norm = np.clip((adx - 20) / 40, 0, 1)  # ADX>60视为强趋势
    result = adx_norm * pos
    result[np.isnan(result)] = 0
    return pd.Series(result, index=data.index).fillna(0).clip(-1,1)
