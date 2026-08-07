"""AI因子: 布林带压缩反转因子 | 置信:60% | 利用布林带宽度收缩后扩张的形态，在价格触及上下轨时产生反转信号。当带宽（上轨-下轨）处于近20日低位且价格突破上轨或下轨时，预期价格回归中轨。计算带宽百分位和价格相对位置生成连续信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerBandCompressionReversal(BaseFactor):
    """利用布林带宽度收缩后扩张的形态，在价格触及上下轨时产生反转信号。当带宽（上轨-下轨）处于近20日低位且价格突破上轨或下轨时，预期价格回归中轨。计算带宽百分位和价格相对位置生成连续信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bb_compress",
            name="Bollinger Band Compression Reversal",
            display_name="布林带压缩反转因子",
            description="利用布林带宽度收缩后扩张的形态，在价格触及上下轨时产生反转信号。当带宽（上轨-下轨）处于近20日低位且价格突破上轨或下轨时，预期价格回归中轨。计算带宽百分位和价格相对位置生成连续信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        # 带宽
        bandwidth = upper - lower
        # 带宽百分位（过去20日内的相对位置）
        rank = bandwidth.rolling(20).apply(lambda x: (x[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=True)
        # 价格相对布林带位置：-1在下轨，0在中轨，1在上轨
        price_pos = (close - ma20) / (std20 * 2 + 1e-10)
        # 信号：当带宽百分位<0.2（压缩）且价格在上轨附近>0.8时看空，在下轨<-0.8时看多
        raw_signal = np.where(rank < 0.2,
                              np.where(price_pos > 0.8, -1.0,
                                       np.where(price_pos < -0.8, 1.0, 0.0)),
                              0.0)
        # 乘上压缩程度（1-rank）作为强度
        intensity = (1 - rank) * 2  # 0~2
        result = raw_signal * np.clip(intensity, 0, 1)
        return result
