"""AI因子: 持仓超时风险 | 置信:60% | 衡量价格在长时间内无法突破窄幅区间导致趋势衰竭的风险。计算过去N根K线的价格波动范围与ATR的比率，当波动率持续萎缩且价格接近均线时产生信号。低波动率且价格无方向时给出负向信号（看跌），反之正向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutRisk(BaseFactor):
    """衡量价格在长时间内无法突破窄幅区间导致趋势衰竭的风险。计算过去N根K线的价格波动范围与ATR的比率，当波动率持续萎缩且价格接近均线时产生信号。低波动率且价格无方向时给出负向信号（看跌），反之正向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout",
            name="Hold Timeout Risk",
            display_name="持仓超时风险",
            description="衡量价格在长时间内无法突破窄幅区间导致趋势衰竭的风险。计算过去N根K线的价格波动范围与ATR的比率，当波动率持续萎缩且价格接近均线时产生信号。低波动率且价格无方向时给出负向信号（看跌），反之正向。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 20
        atr_period = 14
        high, low, close = data['high'], data['low'], data['close']
        # ATR
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(atr_period).mean()
        # 滚动价格区间宽度
        range_width = high.rolling(n).max() - low.rolling(n).min()
        # 波动率压缩比率
        compress = range_width / (atr * np.sqrt(n) + 1e-10)
        # 价格相对于均线的位置
        ma = close.rolling(n).mean()
        pos = (close - ma) / (atr + 1e-10)
        # 当压缩比率低时，价格位置偏向极端则可能突破，否则横盘风险
        sig = np.where(compress < 1.5, -pos * (1.5 - compress), pos * (compress - 1.5))
        # 归一化到[-1,1]
        result = np.clip(sig / 3.0, -1, 1)
        return pd.Series(result, index=data.index)
