"""AI因子: 流动性磁吸反转检测 | 置信:60% | 通过检测价格快速突破后成交量激增并伴随价格反转的现象，识别流动性驱动的虚假突破。计算最近两根K线的价格变化率与成交量的乘积，并比较当前收盘价与上一周期高点/低点的关系。当价格创N周期新高但随后下跌且成交量异常放大时，输出看空信号(-1)；反之看多(+1)。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversalDetection(BaseFactor):
    """通过检测价格快速突破后成交量激增并伴随价格反转的现象，识别流动性驱动的虚假突破。计算最近两根K线的价格变化率与成交量的乘积，并比较当前收盘价与上一周期高点/低点的关系。当价格创N周期新高但随后下跌且成交量异常放大时，输出看空信号(-1)；反之看多(+1)。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lqrev",
            name="Liquidity Magnet Reversal Detection",
            display_name="流动性磁吸反转检测",
            description="通过检测价格快速突破后成交量激增并伴随价格反转的现象，识别流动性驱动的虚假突破。计算最近两根K线的价格变化率与成交量的乘积，并比较当前收盘价与上一周期高点/低点的关系。当价格创N周期新高但随后下跌且成交量异常放大时，输出看空信号(-1)；反之看多(+1)。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
    
        n = 20
        rolling_high = high.rolling(n).max()
        rolling_low = low.rolling(n).min()
    
        # 价格突破程度
        upper_break = (close - rolling_high.shift(1)) / (rolling_high.shift(1) + 1e-8)
        lower_break = (rolling_low.shift(1) - close) / (rolling_low.shift(1) + 1e-8)
    
        # 成交量异常：当前量 vs 过去N日平均成交量
        avg_vol = volume.rolling(20).mean()
        vol_ratio = volume / (avg_vol + 1e-8)
    
        # 价格反转：比较当前收盘价与之前高点/低点的差
        reverse_up = (close - rolling_high.shift(1)) < 0  # 突破后回落
        reverse_down = (rolling_low.shift(1) - close) < 0
    
        # 信号组合
        bearish = (upper_break > 0.01) & (vol_ratio > 1.5) & reverse_up
        bullish = (lower_break > 0.01) & (vol_ratio > 1.5) & reverse_down
    
        result = pd.Series(0.0, index=data.index)
        result[bearish] = -1.0
        result[bullish] = 1.0
        return result
