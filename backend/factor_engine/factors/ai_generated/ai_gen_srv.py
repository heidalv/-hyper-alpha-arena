"""AI因子: 短期反转波动 | 置信:60% | 针对亏损记录中多次因微小波动止损的情况，捕捉价格在极端波动后快速回归均值的倾向。通过计算日内振幅与收盘涨跌幅的比值，并结合近期价格位置，识别超买超卖后的反转机会。正值表示短期超卖可能反弹，负值表示超买可能回调。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Short-term Reversal Volatility(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_srv", name="Short-term Reversal Volatility",
        display_name="短期反转波动", description="针对亏损记录中多次因微小波动止损的情况，捕捉价格在极端波动后快速回归均值的倾向。通过计算日内振幅与收盘涨跌幅的比值，并结合近期价格位置，识别超买超卖后的反转机会。正值表示短期超卖可能反弹，负值表示超买可能回调。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    close = data['close']
    high = data['high']
    low = data['low']
    open_ = data['open']
    # 日内振幅相对于收盘变动
    daily_range = high - low
    daily_change = close - open_
    # 波动扭曲度: 振幅远大于收盘变动说明日内剧烈震荡但收盘回归
    twist = daily_range / (daily_change.abs() + 1e-10)
    # 近期价格位置（5日相对位置）
    high5 = high.rolling(5).max()
    low5 = low.rolling(5).min()
    pos = (close - low5) / (high5 - low5 + 1e-10)
    # 反转信号: 当波动扭曲度高且价格处于极端位置时强烈回归
    signal = np.where(pos > 0.8, -np.clip(twist * 0.1, 0, 1), 0)
    signal = np.where(pos < 0.2, np.clip(twist * 0.1, 0, 1), signal)
    # 平滑后归一化到[-1,1]
    signal = signal.rolling(3).mean()
    max_abs = signal.abs().max()
    if max_abs > 0:
        signal = signal / max_abs
    return signal.fillna(0)
