"""AI因子: 小单平仓反转 | 置信:65% | 识别价格变化微小但成交量异常放大的K线，随后可能出现反向运动。计算最近5根K线内，价格变化率小于0.5%且成交量相对前5日均值放大1.5倍以上，则产生反向信号（当前方向为多头则预期下跌，空头则预期上涨）。返回信号强度为-1到1，正值表示预期上涨，负值预期下跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Tiny Close Reversal(BaseFactor):
    """识别价格变化微小但成交量异常放大的K线，随后可能出现反向运动。计算最近5根K线内，价格变化率小于0.5%且成交量相对前5日均值放大1.5倍以上，则产生反向信号（当前方向为多头则预期下跌，空头则预期上涨）。返回信号强度为-1到1，正值表示预期上涨，负值预期下跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tinyclose",
            name="Tiny Close Reversal",
            display_name="小单平仓反转",
            description="识别价格变化微小但成交量异常放大的K线，随后可能出现反向运动。计算最近5根K线内，价格变化率小于0.5%且成交量相对前5日均值放大1.5倍以上，则产生反向信号（当前方向为多头则预期下跌，空头则预期上涨）。返回信号强度为-1到1，正值表示预期上涨，负值预期下跌。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            # data: DataFrame with columns open, high, low, close, volume
            close = data['close']
            volume = data['volume']
            # 价格变化率（绝对值）
            pct_change = close.pct_change().abs()
            # 成交量相对前5日均值
            vol_ma5 = volume.rolling(5).mean()
            vol_ratio = volume / vol_ma5
            # 条件：价格变化微小且放量
            cond = (pct_change < 0.005) & (vol_ratio > 1.5)
            # 当前方向：用短期动量（前1日价格变化）
            mom = close.diff()
            # 若满足条件且最后根K线为上涨，则预期下跌（负值）；下跌则预期上涨（正值）
            signal = pd.Series(0.0, index=data.index)
            cond_true = cond & (mom < 0)  # 下跌时放量微小，预期反弹
            cond_false = cond & (mom > 0) # 上涨时放量微小，预期回落
            signal[cond_true] = 0.8
            signal[cond_false] = -0.8
            return signal
